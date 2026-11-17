"""Multi-timeframe confluence: combines independent evidence families into BUY/SHORT/WAIT/NO TRADE.

Correlated indicators are not counted twice. Evidence is grouped into three families, each of
which casts at most one vote (+1 bullish, -1 bearish, 0 none or conflicting):

* context  - higher-timeframe structure trend (1h and 15m; they must not conflict)
* location - price inside a still-valid zone (order block, supply/demand, unfilled FVG) on
             5m/15m, or in the discount (long) / premium (short) half of the 15m swing range
* trigger  - a recent confirmed sweep rejection or change of character on 5m/1m

A bias needs the context AND trigger families to agree; location alone never creates a bias,
and overlapping graphical patterns alone never create a trade. Hard gates (news window, missing
or stale data, no risk budget) override everything with NO TRADE.

Outputs are research labels: they are not validated predictions and say nothing about
probability of profit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.research.smc import Analysis, Pattern

ZONES = {
    "bullish_order_block",
    "bearish_order_block",
    "demand_zone",
    "supply_zone",
    "bullish_fvg",
    "bearish_fvg",
    "inverse_fvg",
}
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


@dataclass(frozen=True, slots=True)
class ConfluenceConfig:
    w_context: float = 1.0
    w_location: float = 1.0
    w_trigger: float = 1.0
    #: How recent a trigger must be, in bars of its own timeframe.
    trigger_bars: int = 6
    #: Minimum reward:risk to the first target before a plan is shown as eligible.
    min_rr: float = 1.0
    #: Stops closer than this many ATR(5m) are widened (a 1-tick stop is noise, not risk control).
    min_stop_atr: float = 0.5


def _sign(direction: str) -> int:
    return 1 if direction == "bullish" else -1 if direction == "bearish" else 0


def _recent(p: Pattern, a: Analysis, bars: int) -> bool:
    if p.confirmed_at is None or a.as_of is None:
        return False
    return p.confirmed_at >= a.as_of - timedelta(minutes=TF_MINUTES.get(a.timeframe, 5) * bars)


def _context(analyses: dict[str, Analysis]) -> tuple[int, list[str]]:
    votes, notes = [], []
    for tf in ("1h", "15m"):
        a = analyses.get(tf)
        if a is None:
            notes.append(f"{tf}: no analysis")
            continue
        votes.append(_sign(a.trend))
        notes.append(f"{tf} structure trend {a.trend}")
    nonzero = {v for v in votes if v}
    if len(nonzero) == 1:
        return nonzero.pop(), notes
    if len(nonzero) > 1:
        notes.append("1h and 15m conflict: no context vote")
    return 0, notes


def _location(analyses: dict[str, Analysis], price: float) -> tuple[int, list[str], Pattern | None]:
    for tf in ("5m", "15m"):
        a = analyses.get(tf)
        if a is None:
            continue
        for p in a.patterns:
            if p.type in ZONES and p.status not in ("invalidated", "filled") and p.bottom <= price <= p.top:
                return _sign(p.direction), [f"price inside {tf} {p.type.replace('_', ' ')} {p.bottom:g}-{p.top:g}"], p
    a15 = analyses.get("15m")
    pd = a15.premium_discount if a15 else None
    if pd and pd["zone"] in ("premium", "discount"):
        return (
            (1 if pd["zone"] == "discount" else -1),
            [f"price in the 15m {pd['zone']} ({pd['position']:.0%} of {pd['range_low']:g}-{pd['range_high']:g})"],
            None,
        )
    return 0, ["no valid zone at price and no premium/discount reading"], None


def _trigger(analyses: dict[str, Analysis], cfg: ConfluenceConfig) -> tuple[int, list[str], Pattern | None]:
    for tf in ("5m", "1m"):
        a = analyses.get(tf)
        if a is None:
            continue
        for p in a.patterns:  # most recent first
            if not _recent(p, a, cfg.trigger_bars):
                continue
            if p.type in ("buy_side_sweep", "sell_side_sweep") and p.status == "confirmed_rejection":
                return _sign(p.direction), [f"{tf} {p.type.replace('_', ' ')} rejected at {p.extra['level']:g}"], p
            if p.type in ("CHoCH", "MSS"):
                return _sign(p.direction), [f"{tf} {p.type} {p.direction} through {p.top:g}"], p
    return 0, ["no recent confirmed sweep rejection or change of character on 5m/1m"], None


def confluence(
    symbol: str,
    analyses: dict[str, Analysis],
    price: float | None,
    tick: float,
    news: dict[str, Any],
    risk: dict[str, Any] | None,
    data: dict[str, Any],
    cfg: ConfluenceConfig = ConfluenceConfig(),
    validated: bool = False,
) -> dict[str, Any]:
    gates: list[str] = []
    if price is None or not data.get("available"):
        gates.append("No current market data.")
    if data.get("stale"):
        gates.append("Market data is stale.")
    if news.get("status") == "inside_window":
        gates.append(news.get("summary", "Inside a high-impact event window."))
    if risk is not None and risk.get("known") and (risk.get("risk_budget") or 0) <= 0:
        gates.append("No remaining risk budget on the entered account figures.")

    families: list[dict[str, Any]] = []
    ctx_v, ctx_n = _context(analyses)
    loc_v, loc_n, zone = _location(analyses, price) if price is not None else (0, ["no price"], None)
    trg_v, trg_n, trig = _trigger(analyses, cfg)
    for name, v, notes, w in (
        ("context", ctx_v, ctx_n, cfg.w_context),
        ("location", loc_v, loc_n, cfg.w_location),
        ("trigger", trg_v, trg_n, cfg.w_trigger),
    ):
        families.append({"family": name, "vote": v, "weight": w, "evidence": notes})
    score = sum(f["vote"] * f["weight"] for f in families)
    max_score = cfg.w_context + cfg.w_location + cfg.w_trigger

    direction = 0
    confirmation_required = True
    if ctx_v and ctx_v == trg_v and loc_v != -ctx_v:
        direction, confirmation_required = ctx_v, False
    elif ctx_v and loc_v == ctx_v and trg_v == 0:
        direction = ctx_v  # bias forming; trigger still required
    if gates:
        outcome = "NO TRADE"
    elif direction == 0:
        outcome = "WAIT"
    else:
        outcome = "BUY BIAS" if direction == 1 else "SHORT BIAS"
    headline = outcome + (" — CONFIRMATION REQUIRED" if outcome.endswith("BIAS") and confirmation_required else "")

    plan = None
    if direction and price is not None:
        plan = _plan(direction, price, tick, zone, trig, analyses, cfg)

    return {
        "symbol": symbol,
        "outcome": outcome,
        "headline": headline,
        "confirmation_required": confirmation_required if direction else None,
        "score": score,
        "max_score": max_score,
        "families": families,
        "gates": gates,
        "plan": plan,
        "news": news,
        "data": data,
        "risk": risk,
        "timeframes": {
            tf: {"trend": a.trend, "as_of": a.as_of.isoformat() if a.as_of else None, "patterns": len(a.patterns)}
            for tf, a in analyses.items()
        },
        "validation": "validated" if validated else "RESEARCH ONLY",
        "method": __doc__.split("\n\n")[1].strip() if __doc__ else "",
        "disclaimer": "A heuristic evidence score, not a probability of profit. Never trade on overlapping "
        "patterns alone; confirm with the plan, the risk preview and the news calendar.",
    }


def _plan(
    s: int,
    price: float,
    tick: float,
    zone: Pattern | None,
    trig: Pattern | None,
    analyses: dict[str, Analysis],
    cfg: ConfluenceConfig,
) -> dict[str, Any]:
    def rnd(x: float) -> float:
        return round(round(x / tick) * tick, 6)

    a5 = analyses.get("5m") or next(iter(analyses.values()))
    atr_ = a5.atr or 0.0
    if zone is not None:
        lo, hi = zone.bottom, zone.top
    else:
        lo, hi = sorted((price, price - s * 0.25 * atr_))
    refs = [p.invalidation_price for p in (zone, trig) if p is not None and p.invalidation_price is not None]
    refs = [r for r in refs if (price - r) * s > 0]
    entry = rnd((lo + hi) / 2)
    min_dist = max(cfg.min_stop_atr * atr_, 4 * tick)
    if refs:
        base = min(refs) if s == 1 else max(refs)
        stop = base - s * tick
        stop_basis = "beyond the zone/trigger invalidation level"
        if (entry - stop) * s < min_dist:
            stop = entry - s * min_dist
            stop_basis = f"widened to the minimum {cfg.min_stop_atr} x ATR(5m); structural level was closer"
    else:
        stop = entry - s * 1.5 * atr_
        stop_basis = "1.5 x ATR(5m) (no structural level on the correct side)"
    stop = rnd(stop)
    risk = (entry - stop) * s
    targets = []
    for tf in ("5m", "15m", "1h"):
        a = analyses.get(tf)
        for lv in a.liquidity if a else []:
            if (lv["price"] - entry) * s > 0:
                targets.append((abs(lv["price"] - entry), lv["price"], f"{tf} {lv['name']}"))
    targets.sort()
    tgt = [{"price": rnd(p), "basis": b} for _, p, b in targets[:3]]
    if not tgt and risk > 0:
        tgt = [{"price": rnd(entry + s * 2 * risk), "basis": "2R (no liquidity reference beyond entry)"}]
    rr = round(abs(tgt[0]["price"] - entry) / risk, 2) if tgt and risk > 0 else None
    return {
        "direction": "long" if s == 1 else "short",
        "entry_zone": [rnd(lo), rnd(hi)],
        "entry": entry,
        "entry_conditions": [
            "Price trades into the entry zone.",
            "A 1m/5m close back in the bias direction (rejection) before entering.",
            "No high-impact event inside the configured window.",
        ],
        "stop": stop,
        "stop_basis": stop_basis,
        "targets": tgt,
        "reward_risk": rr,
        "rr_ok": rr is not None and rr >= cfg.min_rr,
        "invalidation": f"A close {'below' if s == 1 else 'above'} {stop:g} invalidates the idea.",
    }
