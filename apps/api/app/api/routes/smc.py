"""Smart-money-concepts analysis, multi-timeframe confluence, liquidity map and SMC opportunity scan.

Read-only. Everything is computed from stored, CLOSED bars of the configured provider and labelled
with that provider's data class (simulated data is never presented as live).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.redis import get_redis
from app.marketdata.calendar import is_market_open
from app.marketdata.instruments import INSTRUMENTS, get_instrument
from app.marketdata.status import read_feed_status
from app.marketdata.store import query_bars
from app.marketdata.types import Timeframe
from app.models.calendar import NewsItem
from app.research.bars import Bar, closed_only
from app.research.confluence import confluence
from app.research.smc import Analysis, SmcConfig, analyze
from app.services.account import current_state
from app.services.calendar import news_exposure
from app.services.research import load_validations

router = APIRouter(prefix="/smc", tags=["smart-money"])

SMC_TFS = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.M30,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)
CONFLUENCE_TFS = (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1)
SCAN_KEY = "apex:smc:scan"
SETUP_STRATEGY = {
    "Liquidity sweep → structure shift": "smc_sweep_mss",
    "Order block retest with trend": "smc_ob_retest",
    "Fair value gap retest": "smc_fvg_retest",
}


def _sym(symbol: str) -> str:
    inst = get_instrument(symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    return inst.symbol


async def closed_bars(session: AsyncSession, symbol: str, tf: Timeframe, limit: int, now: datetime) -> list[Bar]:
    s = get_settings()
    if s.market_data_provider == "none":
        return []
    rows = await query_bars(session, provider=s.market_data_provider, symbol=symbol, timeframe=tf, limit=limit + 1)
    bars = [Bar(b.ts, float(b.open), float(b.high), float(b.low), float(b.close), int(b.volume)) for b in rows]
    return closed_only(bars, tf, now)[-limit:]


async def data_state(session: AsyncSession, symbol: str, now: datetime) -> dict[str, Any]:
    s = get_settings()
    feed = await read_feed_status() or {}
    quality = feed.get("quality") or ("unavailable" if s.market_data_provider == "none" else "unknown")
    last = await closed_bars(session, symbol, Timeframe.M1, 1, now)
    age = (now - (last[-1].ts + Timeframe.M1.delta)).total_seconds() if last else None
    stale = bool(last) and is_market_open(now) and age is not None and age > 180
    return {
        "available": bool(last),
        "provider": s.market_data_provider,
        "quality": quality,
        "last_bar": last[-1].ts.isoformat() if last else None,
        "age_seconds": round(age) if age is not None else None,
        "stale": stale,
        "price": last[-1].close if last else None,
    }


async def _analyses(
    session: AsyncSession, sym: str, now: datetime, tfs: tuple[Timeframe, ...] = CONFLUENCE_TFS
) -> dict[str, Analysis]:
    tick = float(INSTRUMENTS[sym].tick_size)
    out: dict[str, Analysis] = {}
    for tf in tfs:
        bars = await closed_bars(session, sym, tf, 400, now)
        if bars:
            out[tf.value] = analyze(sym, tf.value, bars, tick)
    return out


@router.get("/analysis")
async def analysis(
    _: CurrentUser,
    session: SessionDep,
    symbol: str,
    timeframe: Timeframe = Timeframe.M5,
    limit: Annotated[int, Query(ge=20, le=1500)] = 400,
    pivot: Annotated[int, Query(ge=1, le=10)] = 3,
    min_quality: Annotated[int, Query(ge=0, le=100)] = 0,
    max_zones: Annotated[int, Query(ge=1, le=50)] = 12,
) -> dict[str, Any]:
    if timeframe not in SMC_TFS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Timeframe not supported for SMC analysis")
    sym = _sym(symbol)
    now = datetime.now(UTC)
    bars = await closed_bars(session, sym, timeframe, limit, now)
    data = await data_state(session, sym, now)
    cfg = SmcConfig(pivot_left=pivot, pivot_right=pivot, max_zones=max_zones)
    a = analyze(sym, timeframe.value, bars, float(INSTRUMENTS[sym].tick_size), cfg).as_dict()
    a["patterns"] = [p for p in a["patterns"] if p["quality"] >= min_quality]
    a["data"] = data
    a["config"] = {"pivot_left": pivot, "pivot_right": pivot, "max_zones": max_zones, "min_quality": min_quality}
    return a


async def _confluence(session: AsyncSession, sym: str, now: datetime) -> dict[str, Any]:
    s = get_settings()
    analyses = await _analyses(session, sym, now)
    data = await data_state(session, sym, now)
    news = await news_exposure(session, sym, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes)
    fresh = await _fresh_news(session, sym, now)
    if fresh:
        news = {
            "status": "inside_window",
            "summary": "Fresh published news: " + "; ".join(f"{n.impact} {n.title[:80]}" for n in fresh),
            "events": news.get("events", []),
        }
    _rules, st = await current_state(session, now)
    risk = {
        "known": st.known,
        "risk_budget": float(st.risk_budget) if st.risk_budget is not None else None,
        "rules_verified": st.rules_verified,
        "stale": st.stale,
    }
    out = confluence(sym, analyses, data["price"], float(INSTRUMENTS[sym].tick_size), news, risk, data)
    out["liquidity"] = _merge_liquidity(analyses, data["price"])
    return out


async def _fresh_news(session: AsyncSession, sym: str, now: datetime) -> list[NewsItem]:
    """HIGH/CRITICAL items affecting ``sym`` detected in the last 15 minutes (they force NO TRADE)."""
    rows = (
        await session.scalars(
            select(NewsItem).where(
                NewsItem.impact.in_(("HIGH", "CRITICAL")),
                NewsItem.detected_at >= now - timedelta(minutes=15),
                NewsItem.detected_at <= now,
                NewsItem.published_at >= now - timedelta(hours=2),
            )
        )
    ).all()
    return [n for n in rows if sym in (n.instruments or [])]


@router.get("/confluence")
async def confluence_view(_: CurrentUser, session: SessionDep, symbol: str) -> dict[str, Any]:
    return await _confluence(session, _sym(symbol), datetime.now(UTC))


def _merge_liquidity(analyses: dict[str, Analysis], price: float | None) -> dict[str, Any]:
    weight = {"1m": 0, "5m": 5, "15m": 10, "1h": 15}
    levels: list[dict[str, Any]] = []
    best: dict[float, dict[str, Any]] = {}
    for tf, a in analyses.items():
        if tf == "1m":
            continue  # 1-minute pivots are too noisy for a liquidity map
        for lv in a.liquidity:
            row = {**lv, "timeframe": tf, "score": round(lv["score"] + weight.get(tf, 0), 1)}
            if price is not None:
                # Each timeframe's analysis classified the level against ITS last close; re-classify
                # against the one current price so above/below is consistent.
                if lv["price"] == price:
                    continue
                row["side"] = "above" if lv["price"] > price else "below"
                row["distance"] = round(lv["price"] - price, 6)
            if lv["price"] not in best or row["score"] > best[lv["price"]]["score"]:
                best[lv["price"]] = row
    levels = sorted(best.values(), key=lambda x: -x["score"])
    above = sorted((x for x in levels if x["side"] == "above"), key=lambda x: x["price"])[:5]
    below = sorted((x for x in levels if x["side"] == "below"), key=lambda x: -x["price"])[:5]
    return {
        "price": price,
        "levels": levels[:30],
        "nearest_above": above,
        "nearest_below": below,
        "ranking": "score = type base + 5 x touches + recency (0-20) - 5 x distance in ATR + timeframe weight "
        "(1h 15, 15m 10, 5m 5). A transparent heuristic, not a measure of resting orders.",
        "label": "Inferred price-action levels, not verified resting liquidity.",
    }


@router.get("/liquidity")
async def liquidity(_: CurrentUser, session: SessionDep, symbol: str) -> dict[str, Any]:
    sym = _sym(symbol)
    now = datetime.now(UTC)
    analyses = await _analyses(session, sym, now, (Timeframe.M5, Timeframe.M15, Timeframe.H1))
    data = await data_state(session, sym, now)
    return {"symbol": sym, "data": data, **_merge_liquidity(analyses, data["price"])}


def classify(c: dict[str, Any]) -> list[str]:
    """Named setups present in a confluence result (descriptive; ranking happens separately)."""
    setups: list[str] = []
    fam = {f["family"]: f for f in c["families"]}
    trig = " ".join(fam["trigger"]["evidence"])
    loc = " ".join(fam["location"]["evidence"])
    if "sweep rejected" in trig:
        setups.append(
            "Liquidity sweep → structure shift" if "CHoCH" in trig or "MSS" in trig else "Liquidity sweep reversal"
        )
        if fam["context"]["vote"] == 0:
            setups.append("Range liquidity sweep reversal")
    if "order block" in loc:
        setups.append("Order block retest with trend")
    if "fvg" in loc:
        setups.append("Fair value gap retest")
    if "supply zone" in loc or "demand zone" in loc:
        setups.append("Supply/demand zone retest")
    trends = {v["trend"] for v in c["timeframes"].values()}
    if len(trends) == 1 and trends != {"neutral"} and len(c["timeframes"]) >= 3:
        setups.append("Multi-timeframe structure alignment")
    return setups


@router.get("/scan")
async def scan(_: CurrentUser, session: SessionDep, refresh: bool = False) -> dict[str, Any]:
    """Confluence across every supported instrument. Cached for 60 seconds."""
    redis = get_redis()
    if not refresh:
        try:
            raw = await redis.get(SCAN_KEY)
            if raw:
                return json.loads(raw)
        except (RedisError, OSError):
            pass
    now = datetime.now(UTC)
    validations = await load_validations(session)
    ranked, watch = [], []
    for sym in INSTRUMENTS:
        c = await _confluence(session, sym, now)
        setups = classify(c)
        plan = c["plan"]
        strat_ids = [SETUP_STRATEGY[x] for x in setups if x in SETUP_STRATEGY]
        vals = [validations.get(i) for i in strat_ids]
        validated = any(v is not None and v.validated for v in vals)
        reasons = list(c["gates"])
        if c["outcome"] not in ("BUY BIAS", "SHORT BIAS"):
            reasons.append(f"Outcome {c['outcome']}.")
        if plan and not plan["rr_ok"]:
            reasons.append(f"Reward:risk {plan['reward_risk']} below the minimum.")
        if c["data"].get("quality") not in ("live", "delayed", "historical"):
            reasons.append(f"Data class is {c['data'].get('quality')}: not eligible for ranking.")
        card = {
            "symbol": sym,
            "outcome": c["outcome"],
            "headline": c["headline"],
            "direction": plan["direction"] if plan else None,
            "setups": setups,
            "families": c["families"],
            "score": c["score"],
            "max_score": c["max_score"],
            "entry_zone": plan["entry_zone"] if plan else None,
            "stop": plan["stop"] if plan else None,
            "invalidation": plan["invalidation"] if plan else None,
            "targets": plan["targets"] if plan else [],
            "reward_risk": plan["reward_risk"] if plan else None,
            "validation": "validated" if validated else "RESEARCH ONLY",
            "news": c["news"],
            "risk": c["risk"],
            "data": c["data"],
            "not_ranked_because": reasons,
        }
        (watch if reasons else ranked).append(card)
    ranked.sort(key=lambda x: (-x["score"] * (1 if x["direction"] == "long" else -1), -(x["reward_risk"] or 0)))
    watch.sort(key=lambda x: -abs(x["score"]))
    doc = {
        "as_of": now.isoformat(),
        "ranked": ranked,
        "watch": watch,
        "note": "Ranked only when data quality, outcome, reward:risk and risk checks pass. A heuristic score is "
        "not a probability of profit; unvalidated setups are RESEARCH ONLY.",
    }
    try:
        await redis.set(SCAN_KEY, json.dumps(doc), ex=60)
    except (RedisError, OSError):
        pass
    return doc
