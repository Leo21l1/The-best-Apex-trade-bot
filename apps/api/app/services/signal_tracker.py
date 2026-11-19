"""Signal history: record technical setups once, track them forward, compare 3/5, 4/5 and 5/5.

Nothing here places or simulates orders on any account. Outcomes are HYPOTHETICAL, assessed with
predefined conservative rules on later 1-minute bars:
* entry: a limit at the suggested entry price, filled only when a later bar trades AT or THROUGH it;
* a bar that fills the entry and touches the stop is a loss (stop assumed first; no same-bar target);
* the target needs a trade-through of one tick; flat at 15:55 ET (marked at that bar's close);
* before a fill: a close beyond the stop = INVALIDATED; the target reached, or price running more than
  ``missed_r`` x risk beyond the zone = MISSED; no fill before ``expires_at`` = EXPIRED.
Original signal fields are written once and never changed; only lifecycle columns are updated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.models.signal import SetupSignal
from app.research.bars import Bar
from app.research.stats import trade_stats
from app.services import setups

FLAT_AT = time(15, 55)
RECENT_END_MINUTES = 30


def simulate(sig: dict[str, Any], bars: Sequence[Bar], missed_r: float = 0.5) -> dict[str, Any]:
    """Lifecycle and hypothetical outcome of one recorded setup over 1-minute ``bars`` (ascending).

    Only bars that OPEN at or after ``confirmed_at`` are used (the setup was unknown before that).
    """
    long = sig["direction"] == "long"
    s = 1 if long else -1
    tick = float(INSTRUMENTS[sig["symbol"]].tick_size)
    pv = float(INSTRUMENTS[sig["symbol"]].point_value)
    entry, stop, tp1 = sig["entry"], sig["stop"], sig["tp1"]
    risk = abs(entry - stop)
    net_risk = sig.get("net_risk_usd") or risk * pv
    net_reward = sig.get("net_reward_usd") or abs(tp1 - entry) * pv
    costs = sig.get("costs_usd") or 0.0
    conf, exp = sig["confirmed_at"], sig["expires_at"]
    filled = None
    out: dict[str, Any] = {
        "status": sig.get("status", "CONFIRMED"),
        "status_at": None,
        "status_reason": None,
        "filled_at": None,
        "outcome": None,
        "outcome_at": None,
        "outcome_r": None,
        "outcome_usd": None,
    }
    for b in bars:
        if b.ts < conf:
            continue
        local = (b.ts + timedelta(minutes=1)).astimezone(ET)
        if filled is None:
            if b.ts >= exp:
                out.update(status="EXPIRED", status_at=b.ts, status_reason="entry not reached before expiry")
                return out
            if (b.low <= entry) if long else (b.high >= entry):
                filled = b.ts
                out["filled_at"] = b.ts
                if (b.low <= stop) if long else (b.high >= stop):
                    out.update(outcome="stop", outcome_at=b.ts, outcome_r=-1.0, outcome_usd=round(-net_risk, 2))
                    return out
                continue
            if (b.close < stop) if long else (b.close > stop):
                out.update(status="INVALIDATED", status_at=b.ts, status_reason="closed beyond the stop before entry")
                return out
            ran = (b.high - sig["entry_high"]) if long else (sig["entry_low"] - b.low)
            if ((b.high >= tp1) if long else (b.low <= tp1)) or ran > missed_r * risk:
                out.update(status="MISSED", status_at=b.ts, status_reason="price left the entry zone toward the target")
                return out
            continue
        if (b.low <= stop) if long else (b.high >= stop):
            if (b.open < stop) if long else (b.open > stop):
                # Gapped through the stop: the stop market order fills at the open, not at the stop price.
                usd = (b.open - entry) * s * pv - costs
                out.update(
                    outcome="stop", outcome_at=b.ts, outcome_r=round(usd / net_risk, 3), outcome_usd=round(usd, 2)
                )
                return out
            out.update(outcome="stop", outcome_at=b.ts, outcome_r=-1.0, outcome_usd=round(-net_risk, 2))
            return out
        if (b.high >= tp1 + tick) if long else (b.low <= tp1 - tick):
            out.update(
                outcome="tp1",
                outcome_at=b.ts,
                outcome_r=round(net_reward / net_risk, 3),
                outcome_usd=round(net_reward, 2),
            )
            return out
        if local.time() >= FLAT_AT and local.date() == (filled + timedelta(minutes=1)).astimezone(ET).date():
            usd = (b.close - entry) * s * pv - costs
            out.update(outcome="eod", outcome_at=b.ts, outcome_r=round(usd / net_risk, 3), outcome_usd=round(usd, 2))
            return out
    return out


def _key(symbol: str, cand: dict[str, Any]) -> str:
    tier = "Q" if cand["confirmed"] else "R"
    return f"{symbol}|{cand['strategy_id']}|{cand['direction']}|{cand.get('signal_bar')}|{tier}"


def recordable(cand: dict[str, Any]) -> bool:
    lv = cand["levels"]
    return cand["score"] >= 3 and lv["stop"] is not None and lv["tp1"] is not None and bool(lv["rr_tp1"])


async def record(session: AsyncSession, doc: dict[str, Any], now: datetime, expiry_minutes: int) -> list[SetupSignal]:
    """Store each new candidate once: qualified setups (CONFIRMED) and >= 3/5 research observations."""
    data = doc.get("data") or {}
    if data.get("label") not in setups.FRESH_DATA or not data.get("price_time"):
        return []
    known_at = datetime.fromisoformat(data["price_time"])  # close of the last candle used
    new: list[SetupSignal] = []
    for cand in (doc.get("signal") or {}).get("candidates", []):
        if not recordable(cand):
            continue
        lv = cand["levels"]
        rr = lv["rr_tp1"]
        original = {
            "engine_version": setups.ENGINE_VERSION,
            "candidate": cand,
            "news": {k: (doc.get("news") or {}).get(k) for k in ("status", "summary", "as_of")},
            "data": data,
            "context": doc.get("context"),
            "confirmed_at_et": known_at.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "costs_usd": rr.get("costs"),
        }
        row = {
            "key": _key(doc["symbol"], cand),
            "symbol": doc["symbol"],
            "contract": data.get("contract"),
            "data_class": (data.get("quality") or "unknown")[:16],
            "strategy_id": cand["strategy_id"],
            "engine_version": setups.ENGINE_VERSION,
            "direction": cand["direction"],
            "score": cand["score"],
            "qualified": cand["confirmed"],
            "evidence": cand["evidence"],
            "trigger_bar": datetime.fromisoformat(cand["signal_bar"]) if cand.get("signal_bar") else None,
            "confirmed_at": known_at,
            "detected_at": now,
            "expires_at": known_at + timedelta(minutes=expiry_minutes),
            "entry": lv["entry"],
            "entry_low": lv["entry_zone"][0],
            "entry_high": lv["entry_zone"][1],
            "stop": lv["stop"],
            "tp1": lv["tp1"],
            "tp2": lv["tp2"],
            "net_rr_tp1": rr.get("net"),
            "net_risk_usd": rr.get("net_risk"),
            "net_reward_usd": rr.get("net_reward"),
            "original": original,
            "status": "CONFIRMED" if cand["confirmed"] else "OBSERVED",
        }
        res = await session.execute(
            insert(SetupSignal).values(**row).on_conflict_do_nothing(index_elements=["key"]).returning(SetupSignal.id)
        )
        rid = res.scalar()
        if rid is not None:
            new.append(await session.get(SetupSignal, rid))  # type: ignore[arg-type]
    return [r for r in new if r is not None]


def _as_dict(r: SetupSignal) -> dict[str, Any]:
    return {
        "symbol": r.symbol,
        "direction": r.direction,
        "entry": r.entry,
        "entry_low": r.entry_low,
        "entry_high": r.entry_high,
        "stop": r.stop,
        "tp1": r.tp1,
        "confirmed_at": r.confirmed_at,
        "expires_at": r.expires_at,
        "net_risk_usd": r.net_risk_usd,
        "net_reward_usd": r.net_reward_usd,
        "costs_usd": (r.original or {}).get("costs_usd"),
        "status": r.status,
    }


async def update(session: AsyncSession, symbol: str, provider: str, now: datetime) -> int:
    """Advance the lifecycle of open setups with bars that have CLOSED by ``now``."""
    from app.research.scanner import load_bars

    rows = (
        await session.scalars(
            select(SetupSignal).where(
                SetupSignal.symbol == symbol,
                SetupSignal.status.in_(("CONFIRMED", "OBSERVED")),
                SetupSignal.outcome.is_(None),
                SetupSignal.confirmed_at >= now - timedelta(days=3),
            )
        )
    ).all()
    if not rows:
        return 0
    start = min(r.confirmed_at for r in rows)
    bars = [b for b in await load_bars(session, provider, symbol, start, now) if b.ts + timedelta(minutes=1) <= now]
    changed = 0
    for r in rows:
        res = simulate(_as_dict(r), bars)
        if res["status"] != r.status or res["outcome"] != r.outcome or res["filled_at"] != r.filled_at:
            for k, v in res.items():
                setattr(r, k, v)
            changed += 1
    return changed


async def invalidate_on_structure(session: AsyncSession, doc: dict[str, Any], now: datetime) -> int:
    """Before entry, an opposite confirmed 5m internal structure shift after confirmation invalidates a setup."""
    ev = (((doc.get("advanced") or {}).get("5m") or {}).get("internal") or {}).get("events") or []
    if not ev:
        return 0
    last = ev[-1]
    t = datetime.fromisoformat(last["time"])
    rows = (
        await session.scalars(
            select(SetupSignal).where(
                SetupSignal.symbol == doc["symbol"],
                SetupSignal.status.in_(("CONFIRMED", "OBSERVED")),
                SetupSignal.filled_at.is_(None),
                SetupSignal.outcome.is_(None),
                SetupSignal.confirmed_at < t,
            )
        )
    ).all()
    n = 0
    for r in rows:
        against = "bearish" if r.direction == "long" else "bullish"
        if last["direction"] == against and now < r.expires_at:
            r.status, r.status_at = "INVALIDATED", t
            r.status_reason = f"opposite 5m {last['kind']} through {last['level']:g} before entry"
            n += 1
    return n


def public(r: SetupSignal) -> dict[str, Any]:
    cand = (r.original or {}).get("candidate") or {}
    return {
        "id": r.id,
        "symbol": r.symbol,
        "contract": r.contract,
        "strategy_id": r.strategy_id,
        "direction": r.direction,
        "score": r.score,
        "qualified": r.qualified,
        "evidence": r.evidence,
        "engine_version": r.engine_version,
        "confirmed_at": r.confirmed_at.isoformat(),
        "confirmed_at_et": r.confirmed_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
        "expires_at": r.expires_at.isoformat(),
        "expires_at_et": r.expires_at.astimezone(ET).strftime("%H:%M ET"),
        "entry": r.entry,
        "entry_zone": [r.entry_low, r.entry_high],
        "stop": r.stop,
        "tp1": r.tp1,
        "tp2": r.tp2,
        "net_rr_tp1": r.net_rr_tp1,
        "risk_usd_1c": r.net_risk_usd,
        "risk_warning": (cand.get("levels") or {}).get("risk_warning"),
        "rr_tp2": (cand.get("levels") or {}).get("rr_tp2"),
        "stop_basis": (cand.get("levels") or {}).get("stop_basis"),
        "tp1_basis": (cand.get("levels") or {}).get("tp1_basis"),
        "tp2_basis": (cand.get("levels") or {}).get("tp2_basis"),
        "factors": cand.get("factors", []),
        "status": r.status,
        "status_at": r.status_at.isoformat() if r.status_at else None,
        "status_reason": r.status_reason,
        "filled_at": r.filled_at.isoformat() if r.filled_at else None,
        "outcome": r.outcome,
        "outcome_r": r.outcome_r,
        "data_class": r.data_class,
    }


async def display(session: AsyncSession, symbol: str, now: datetime) -> dict[str, Any] | None:
    """The recorded qualified setup to show now: an active one, or one that ended in the last 30 min."""
    rows = (
        await session.scalars(
            select(SetupSignal)
            .where(
                SetupSignal.symbol == symbol,
                SetupSignal.qualified.is_(True),
                SetupSignal.confirmed_at >= now - timedelta(hours=8),
            )
            .order_by(SetupSignal.confirmed_at.desc())
            .limit(5)
        )
    ).all()
    for r in rows:
        if r.status == "CONFIRMED" and r.outcome is None and (r.filled_at is not None or now < r.expires_at):
            return {
                **public(r),
                "display_state": f"TECHNICAL {'BUY' if r.direction == 'long' else 'SELL'} SETUP CONFIRMED",
                "entered": r.filled_at is not None,
            }
    for r in rows:
        ended = r.status_at or r.outcome_at
        if (
            r.status in ("INVALIDATED", "EXPIRED", "MISSED")
            and ended
            and now - ended <= timedelta(minutes=RECENT_END_MINUTES)
        ):
            return {**public(r), "display_state": r.status, "entered": False}
    return None


# --------------------------------------------------------------------------- threshold comparison


@dataclass
class Outcome:
    pnl: float
    r: float
    exit_ts: str
    trading_day: str
    commission: float
    strategy: str
    symbol: str
    session: str
    news: str
    score: int
    month: str
    regime: str = "unknown"


def outcome_rows(items: Sequence[dict[str, Any]]) -> list[Outcome]:
    """Resolved hypothetical trades (tp1 / stop / eod) from signal dicts with outcome fields."""
    out = []
    for x in items:
        if x.get("outcome") not in ("tp1", "stop", "eod"):
            continue
        ts = x["outcome_at"] if isinstance(x["outcome_at"], datetime) else datetime.fromisoformat(x["outcome_at"])
        conf = (
            x["confirmed_at"] if isinstance(x["confirmed_at"], datetime) else datetime.fromisoformat(x["confirmed_at"])
        )
        t = conf.astimezone(ET).time()
        sess = "09:30-11:30" if t < time(11, 30) else "11:30-13:30" if t < time(13, 30) else "13:30-16:00"
        out.append(
            Outcome(
                x["outcome_usd"],
                x["outcome_r"],
                ts.isoformat(),
                trading_day(ts).isoformat(),
                x.get("costs_usd") or 0.0,
                x["strategy_id"],
                x["symbol"],
                sess,
                x.get("news_status") or "unknown",
                x["score"],
                trading_day(ts).isoformat()[:7],
                x.get("regime") or "unknown",
            )
        )
    return sorted(out, key=lambda o: o.exit_ts)


def _summ(trades: Sequence[Outcome]) -> dict[str, Any]:
    st = trade_stats(trades)
    keep = (
        "trades",
        "win_rate",
        "win_rate_ci95",
        "avg_win",
        "avg_loss",
        "expectancy",
        "expectancy_ci",
        "avg_r",
        "profit_factor",
        "net_pnl",
        "max_drawdown",
        "max_consecutive_losses",
        "monthly",
        "profitable_months_pct",
    )
    return {k: st.get(k) for k in keep}


def compare_thresholds(items: Sequence[dict[str, Any]], counts: dict[int, int] | None = None) -> dict[str, Any]:
    """3/5 (>=3), 4/5 (>=4) and 5/5 thresholds on the same outcome rules, with uncertainty."""
    rows = outcome_rows(items)
    tiers = {"3/5 loose (research only)": 3, "4/5 balanced (live threshold)": 4, "5/5 strict": 5}
    res: dict[str, Any] = {}
    for name, k in tiers.items():
        sub = [o for o in rows if o.score >= k]
        res[name] = {
            "signals": sum(v for s, v in counts.items() if s >= k)
            if counts
            else sum(1 for x in items if x["score"] >= k),
            "resolved": len(sub),
            **_summ(sub),
            "by_strategy": {g: _summ([o for o in sub if o.strategy == g]) for g in sorted({o.strategy for o in sub})},
            "by_instrument": {g: _summ([o for o in sub if o.symbol == g]) for g in sorted({o.symbol for o in sub})},
            "by_session": {g: _summ([o for o in sub if o.session == g]) for g in sorted({o.session for o in sub})},
            "by_news": {g: _summ([o for o in sub if o.news == g]) for g in sorted({o.news for o in sub})},
            "by_regime": {g: _summ([o for o in sub if o.regime == g]) for g in sorted({o.regime for o in sub})},
        }
    exact = {f"exactly {k}/5": _summ([o for o in rows if o.score == k]) for k in (3, 4, 5)}
    return {
        "tiers": res,
        "exact": exact,
        "verdict": verdict(res),
        "method": "Hypothetical limit entry at the suggested price, stop first on ambiguous bars, TP1 needs a "
        "1-tick trade-through, flat 15:55 ET; P&L after commission and slippage, 1 contract.",
        "caveat": "Tiers are nested (every 5/5 is also >=4/5). A confluence count is a checklist, not a "
        "probability; differences inside overlapping intervals are not evidence.",
    }


MIN_COMPARE = 30


def verdict(res: dict[str, Any]) -> str:
    small = [k for k, v in res.items() if (v.get("resolved") or 0) < MIN_COMPARE]
    vals = {k: v for k, v in res.items() if v.get("expectancy_ci") and k not in small}
    if len(vals) < 2:
        return "INCONCLUSIVE: too few resolved outcomes to compare thresholds (" + ", ".join(small) + " below 30)."
    names = list(vals)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = vals[names[i]]["expectancy_ci"], vals[names[j]]["expectancy_ci"]
            if a[0] > b[1] or b[0] > a[1]:
                better = names[i] if a[0] > b[1] else names[j]
                return (
                    f"{better} has a higher expectancy with non-overlapping 95% intervals; confirm on new "
                    "prospective data before changing the live threshold."
                )
    return (
        "INCONCLUSIVE: expectancy intervals overlap for every threshold; keep 4/5 and keep collecting "
        "prospective results." + (f" Small samples: {', '.join(small)}." if small else "")
    )


async def forward_stats(session: AsyncSession, days: int = 365) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(SetupSignal).where(SetupSignal.confirmed_at >= datetime.now(UTC) - timedelta(days=days))
        )
    ).all()
    items = [
        {
            **public(r),
            "outcome_at": r.outcome_at,
            "confirmed_at": r.confirmed_at,
            "outcome_usd": r.outcome_usd,
            "costs_usd": (r.original or {}).get("costs_usd"),
            "news_status": ((r.original or {}).get("news") or {}).get("status"),
        }
        for r in rows
    ]
    counts: dict[int, int] = {}
    for r in rows:
        counts[r.score] = counts.get(r.score, 0) + 1
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    events = group_opportunities(items)
    return {
        "recorded": len(rows),
        "qualified": sum(1 for r in rows if r.qualified),
        "opportunities": len(events),
        "multi_strategy_opportunities": sum(1 for e in events if e["detections"] > 1),
        "by_status": by_status,
        # Combined performance counts each market opportunity once (strategies that detected the same plan
        # are listed together); strategy-level detection results are reported separately.
        "comparison": compare_thresholds(events),
        "strategy_detections": compare_thresholds(items, counts),
        "label": "Prospective, hypothetical outcomes of recorded technical setups (no orders were placed).",
    }


# ------------------------------------------------------------------------------ opportunities
#
# Several strategies can detect the SAME executable plan: the setup engine builds levels from market
# structure, so detections on the same 5m close in the same direction get identical entry/stop/TP1.
# Those are one market opportunity, not independent trades. Plans that differ by even one tick in
# entry, stop or target, or occur at a different time, are kept separate (they are different trades);
# their simultaneous exposure is reported by ``one_position_at_a_time`` and ``exposure``.


def _ts(v: Any) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(v)


def opportunity_id(x: dict[str, Any]) -> str:
    import hashlib

    tick = float(INSTRUMENTS[x["symbol"]].tick_size)
    key = "|".join(
        [
            x["symbol"],
            x["direction"],
            _ts(x["confirmed_at"]).astimezone(UTC).strftime("%Y-%m-%dT%H:%M"),
            *(str(round(float(x[k]) / tick)) for k in ("entry", "stop", "tp1")),
        ]
    )
    return "opp-" + hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]


def group_opportunities(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per opportunity, preserving every detecting strategy. Members' outcomes must agree (same plan,
    same bars); a disagreement is flagged as a calculation defect rather than silently resolved."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for x in items:
        groups.setdefault(opportunity_id(x), []).append(x)
    out = []
    for oid, xs in groups.items():
        rep = max(xs, key=lambda x: (x["score"], x["strategy_id"]))
        outcomes = {(x.get("status"), x.get("outcome"), x.get("outcome_usd")) for x in xs}
        out.append(
            {
                **rep,
                "opportunity_id": oid,
                "strategies": sorted({x["strategy_id"] for x in xs}),
                "strategy_id": "+".join(sorted({x["strategy_id"] for x in xs})),
                "detections": len(xs),
                "score": max(x["score"] for x in xs),
                "consistent": len(outcomes) == 1,
            }
        )
    return sorted(out, key=lambda e: (_ts(e["confirmed_at"]), e["opportunity_id"]))


def _end(x: dict[str, Any]) -> datetime:
    """When the opportunity stops occupying the trader: exit, end of the setup, or expiry."""
    for k in ("outcome_at", "status_at"):
        if x.get(k):
            return _ts(x[k])
    if x.get("filled_at"):
        return _ts(x["filled_at"]) + timedelta(days=1)  # open position without a recorded exit
    return _ts(x["expires_at"])


def one_position_at_a_time(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Executable sequence for one account: an opportunity is taken only if nothing is pending or open.

    Pre-registered, outcome-blind tie-break at the same time: higher score, then smaller planned risk.
    """
    order = sorted(
        events,
        key=lambda e: (_ts(e["confirmed_at"]), -e["score"], e.get("net_risk_usd") or 0, e.get("opportunity_id", "")),
    )
    taken: list[dict[str, Any]] = []
    busy = None
    for e in order:
        if busy is not None and _ts(e["confirmed_at"]) < busy:
            continue
        taken.append(e)
        busy = _end(e)
    return taken


def exposure(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Hypothetical simultaneous exposure if every row were executed as its own 1-contract position."""
    ivs = [(_ts(x["filled_at"]), _end(x), x.get("net_risk_usd") or 0.0) for x in items if x.get("filled_at")]
    pts = sorted([(a, 1, r) for a, _, r in ivs] + [(b, -1, r) for _, b, r in ivs], key=lambda p: (p[0], p[1]))
    cur = peak = 0
    risk = peak_risk = 0.0
    for _, d, r in pts:
        cur += d
        risk += d * r
        peak, peak_risk = max(peak, cur), max(peak_risk, risk)
    return {
        "positions": len(ivs),
        "max_concurrent_positions": peak,
        "max_concurrent_planned_risk_usd": round(peak_risk, 2),
    }
