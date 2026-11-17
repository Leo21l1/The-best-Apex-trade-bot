"""Historical validation harness for SMC pattern detectors.

For every directional pattern, the hypothetical entry is the OPEN of the first bar after the
pattern's confirmation bar (never earlier), held for ``horizon`` bars, in the pattern's direction,
minus round-trip costs. Results are compared with a baseline that enters in the same direction at
every bar (the unconditional drift), and split chronologically into in-sample / out-of-sample.

Detection uses the full series once; this is safe because a pattern's identity, direction and
confirmation time depend only on bars up to confirmation (property-tested in test_p9_smc).
Statuses such as "invalidated" are hindsight and are NOT used here.

A detector is labelled ``predictive`` only when, after costs, both halves have at least
``min_trades`` occurrences, positive expectancy and beat the baseline. Everything else is
RESEARCH ONLY. Nothing is optimised: thresholds are fixed and reported.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.research.bars import Bar
from app.research.smc import SmcConfig, analyze

DIRECTIONAL = {
    "buy_side_sweep",
    "sell_side_sweep",
    "BOS",
    "CHoCH",
    "MSS",
    "bullish_order_block",
    "bearish_order_block",
    "bullish_fvg",
    "bearish_fvg",
    "inverse_fvg",
    "demand_zone",
    "supply_zone",
    "breakout_retest",
    "momentum_divergence",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    n: int
    win_rate: float | None
    expectancy: float | None  # points per trade after costs
    profit_factor: float | None
    max_drawdown: float  # points, from cumulative sequence


def _summ(rets: Sequence[float]) -> Outcome:
    if not rets:
        return Outcome(0, None, None, None, 0.0)
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    cum = peak = dd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return Outcome(
        len(rets),
        round(len(wins) / len(rets), 3),
        round(sum(rets) / len(rets), 4),
        round(sum(wins) / sum(losses), 3) if losses else None,
        round(dd, 4),
    )


def pattern_stats(
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    tick: float,
    horizon: int = 12,
    cost_ticks: float = 2.0,
    split: float = 0.7,
    min_trades: int = 30,
    cfg: SmcConfig = SmcConfig(max_zones=10**6),
) -> dict[str, Any]:
    bars = list(bars)
    a = analyze(symbol, timeframe, bars, tick, cfg)
    index = {b.ts: i for i, b in enumerate(bars)}
    cost = cost_ticks * tick
    cut = int(len(bars) * split)
    per: dict[str, dict[str, list[float]]] = {}
    seen: set[tuple[str, int]] = set()
    for p in a.patterns:
        if p.type not in DIRECTIONAL or p.direction == "neutral" or p.confirmed_at is None:
            continue
        i = index.get(p.confirmed_at)
        if i is None or i + 1 + horizon >= len(bars):
            continue
        key = (p.type, i)
        if key in seen:  # one occurrence per type per bar
            continue
        seen.add(key)
        s = 1 if p.direction == "bullish" else -1
        entry, exit_ = bars[i + 1].open, bars[i + 1 + horizon].open
        r = (exit_ - entry) * s - cost
        name = f"{p.type}:{p.direction}"
        bucket = per.setdefault(name, {"is": [], "oos": []})
        bucket["is" if i < cut else "oos"].append(r)

    def baseline(s: int, lo: int, hi: int) -> Outcome:
        return _summ(
            [
                (bars[i + 1 + horizon].open - bars[i + 1].open) * s - cost
                for i in range(lo, min(hi, len(bars) - horizon - 1))
            ]
        )

    base = {
        "bullish": {"is": baseline(1, 0, cut), "oos": baseline(1, cut, len(bars))},
        "bearish": {"is": baseline(-1, 0, cut), "oos": baseline(-1, cut, len(bars))},
    }
    rows = []
    for name, b in sorted(per.items()):
        d = name.split(":")[1]
        ins, oos = _summ(b["is"]), _summ(b["oos"])
        bi, bo = base[d]["is"], base[d]["oos"]
        predictive = (
            ins.n >= min_trades
            and oos.n >= min_trades
            and (ins.expectancy or 0) > 0
            and (oos.expectancy or 0) > 0
            and (ins.expectancy or 0) > (bi.expectancy or 0)
            and (oos.expectancy or 0) > (bo.expectancy or 0)
        )
        rows.append(
            {
                "pattern": name,
                "in_sample": asdict(ins),
                "out_of_sample": asdict(oos),
                "baseline_in_sample": asdict(bi),
                "baseline_out_of_sample": asdict(bo),
                "label": "predictive (on this data)" if predictive else "RESEARCH ONLY",
                "sample_note": None
                if ins.n >= min_trades and oos.n >= min_trades
                else f"Fewer than {min_trades} occurrences in a split: not interpretable.",
            }
        )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": len(bars),
        "from": bars[0].ts.isoformat() if bars else None,
        "to": bars[-1].ts.isoformat() if bars else None,
        "method": {
            "entry": "open of the bar after confirmation",
            "exit": f"open {horizon} bars later",
            "costs": f"{cost_ticks} ticks round trip",
            "split": f"first {int(split * 100)}% in-sample, rest out-of-sample (chronological)",
            "baseline": "same direction entered at every bar",
            "min_trades": min_trades,
        },
        "patterns": rows,
        "warning": "Results on simulated data say nothing about real markets. Even on real data, a positive result "
        "is historical, not a guarantee.",
    }
