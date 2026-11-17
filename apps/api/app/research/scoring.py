"""Setup-quality score and opportunity assembly.

The score is a deterministic 0-100 checklist of observable setup features. It is NOT a
probability of profit and is not calibrated; it only orders setups that already passed a
strategy's rules. Ranking additionally requires validated strategies, real data and risk
eligibility.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.core.config import Settings
from app.marketdata.types import Timeframe
from app.research.validation import strategy_validation
from app.strategies.base import Evaluation, MarketContext, Signal

SCORE_NOTE = "Setup-quality checklist score (0-100). Not a probability and not calibrated."


def round_trip_cost(ctx: MarketContext, settings: Settings) -> float:
    """Assumed cost in dollars to enter and exit one contract."""
    inst = ctx.instrument
    commission = (
        settings.cost_commission_per_side_micro
        if inst.is_micro
        else settings.cost_commission_per_side_standard
    )
    slippage = settings.cost_slippage_ticks_per_side * float(inst.tick_value)
    return round(2 * (commission + slippage), 2)


def score_signal(ctx: MarketContext, sig: Signal, cost: float) -> tuple[float, list[dict[str, Any]]]:
    s = 1 if sig.direction == "long" else -1
    parts: list[dict[str, Any]] = []

    def add(name: str, points: float, maximum: float, detail: str) -> None:
        parts.append({"name": name, "points": round(points, 1), "max": maximum, "detail": detail})

    rr = sig.reward_risk
    add("Reward:risk to first target", 25 * min(rr / 2.0, 1.0), 25, f"{rr:.2f}R")

    aligned, notes = 0.0, []
    for tf in (Timeframe.M15, Timeframe.H1):
        t = ctx.trend(tf)
        notes.append(f"{tf.value} {'unknown' if t is None else {1: 'up', -1: 'down', 0: 'flat'}[t]}")
        aligned += 12.5 if t == s else 0.0
    add("Higher-timeframe trend agreement", aligned, 25, ", ".join(notes))

    avg = ctx.vol_sma20[-2] if len(ctx.vol_sma20) >= 2 else None
    ratio = ctx.m5[-1].volume / avg if avg else 0.0
    add("Relative volume", 15 * min(max(ratio - 0.5, 0.0), 1.0), 15, f"{ratio:.2f} x 20-bar average")

    risk_dollars = sig.risk / ctx.tick * float(ctx.instrument.tick_value)
    cost_ratio = cost / risk_dollars if risk_dollars > 0 else 1.0
    add(
        "Cost efficiency",
        20 * (1 - min(cost_ratio / 0.25, 1.0)),
        20,
        f"assumed cost ${cost:.2f} = {cost_ratio:.0%} of ${risk_dollars:.2f} risk per contract",
    )

    v = ctx.vwap[0][-1]
    if sig.strategy_id == "mean_reversion" or v is None:
        add("Location vs VWAP", 7.5, 15, "neutral (counter-VWAP strategy or VWAP unavailable)")
    else:
        ok = (sig.entry - v) * s > 0
        add("Location vs VWAP", 15.0 if ok else 0.0, 15, f"entry {sig.entry} vs VWAP {v:.2f}")

    return round(sum(p["points"] for p in parts), 1), parts


def build_opportunity(
    ctx: MarketContext, ev: Evaluation, settings: Settings, provider: str, quality: str
) -> dict[str, Any]:
    """Explainable opportunity record for a triggered signal."""
    sig = ev.signal
    assert sig is not None
    cost = round_trip_cost(ctx, settings)
    score, breakdown = score_signal(ctx, sig, cost)
    val = strategy_validation(sig.strategy_id)
    tick_value = float(ctx.instrument.tick_value)
    risk_dollars = round(sig.risk / ctx.tick * tick_value, 2)

    blockers = []
    if not val.validated:
        blockers.append(f"Strategy not validated: {val.reason}")
    if quality not in ("live", "delayed"):
        blockers.append(f"Data class is {quality.upper()}; ranking requires live or delayed data.")
    blockers.append("Risk eligibility not evaluated (Risk Guardian arrives in Phase 4).")

    uncertainty = [SCORE_NOTE]
    if quality == "simulated":
        uncertainty.append("Prices are SIMULATED. This is not a real market setup.")
    if val.sample_size is None:
        uncertainty.append("No historical sample: expectancy and win rate are unknown.")

    return {
        "strategy_id": sig.strategy_id,
        "symbol": sig.symbol,
        "direction": sig.direction,
        "bar_ts": sig.bar_ts.isoformat(),
        "data_ts": ctx.now.isoformat(),
        "provider": provider,
        "quality": quality,
        "entry": sig.entry,
        "entry_low": sig.entry_low,
        "entry_high": sig.entry_high,
        "stop": sig.stop,
        "targets": list(sig.targets),
        "reward_risk": round(sig.reward_risk, 2),
        "risk_per_contract": risk_dollars,
        "estimated_cost_per_contract": cost,
        "loss_at_stop_after_costs": round(risk_dollars + cost, 2),
        "score": score,
        "score_breakdown": breakdown,
        "conditions": [asdict(c) for c in ev.conditions],
        "context": {
            "trend": {tf.value: ctx.trend(tf) for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1)},
            "atr_5m": ctx.atr14[-1],
            "vwap": ctx.vwap[0][-1],
            "rsi_5m": ctx.rsi14[-1],
        },
        "validation": {
            "validated": val.validated,
            "reason": val.reason,
            "sample_size": val.sample_size,
            "expectancy_r": val.expectancy_r,
        },
        "news_exposure": "unknown (economic calendar not connected until Phase 5)",
        "risk_eligibility": "not evaluated",
        "uncertainty": uncertainty,
        "rank_eligible": False,
        "rank_blockers": blockers,
    }
