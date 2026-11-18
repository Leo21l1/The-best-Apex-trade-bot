"""Trade statistics with uncertainty. Pure and deterministic (seeded bootstrap).

Definitions (also printed in reports):
* A trade WINS when its net P&L (after commission and slippage) is > 0, LOSES when < 0, and is
  BREAKEVEN when exactly 0. Win rate = wins / all trades (breakevens count in the denominator, so
  the rate is never flattered by dropping them).
* Win-rate interval: Wilson score interval (95%). The conservative "lower bound" is used to rank
  win rates so that 9/10 does not outrank 400/600 on a small sample.
* Expectancy interval: percentile bootstrap of the mean net P&L per trade (2,000 resamples, seed 7),
  optionally at a Bonferroni-adjusted confidence level when many configurations are compared.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import mean, median
from typing import Any

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def bootstrap_mean_ci(
    xs: Sequence[float], level: float = 0.95, n: int = 2000, seed: int = 7
) -> tuple[float, float] | None:
    if len(xs) < 2:
        return None
    import numpy as np

    rng = np.random.default_rng(seed)
    a = np.asarray(xs, dtype=float)
    means = a[rng.integers(0, len(a), size=(n, len(a)))].mean(axis=1)
    lo, hi = np.quantile(means, [(1 - level) / 2, (1 + level) / 2])
    return (round(float(lo), 2), round(float(hi), 2))


def _streak(trades: Sequence[Any], losing: bool) -> int:
    best = cur = 0
    for t in trades:
        hit = t.pnl < 0 if losing else t.pnl > 0
        cur = cur + 1 if hit else 0
        best = max(best, cur)
    return best


def trade_stats(trades: Sequence[Any], sessions: int | None = None, ci_level: float = 0.95) -> dict[str, Any]:
    """Every per-group statistic used by the leaderboards. ``trades`` must be in time order."""
    n = len(trades)
    pnl = [t.pnl for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    be = n - len(wins) - len(losses)
    gp, gl = sum(wins), -sum(losses)
    equity = peak = dd = 0.0
    curve = []
    for t in trades:
        equity += t.pnl
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        curve.append((t.exit_ts, round(equity, 2), round(peak - equity, 2)))
    daily: dict[str, float] = defaultdict(float)
    monthly: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        daily[t.trading_day] += t.pnl
        monthly[t.trading_day[:7]].append(t.pnl)
    costs = sum(t.commission + getattr(t, "slippage_usd", 0.0) for t in trades)
    gross_ideal = sum(getattr(t, "gross_pnl", t.pnl) for t in trades if getattr(t, "gross_pnl", t.pnl) > 0)
    rs = [t.r for t in trades]
    wr = len(wins) / n if n else None
    wci = wilson(len(wins), n)
    out: dict[str, Any] = {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": be,
        "win_rate": round(wr, 4) if wr is not None else None,
        "win_rate_ci95": [round(wci[0], 4), round(wci[1], 4)] if wci else None,
        "win_rate_lower_bound": round(wci[0], 4) if wci else None,
        "avg_win": round(mean(wins), 2) if wins else None,
        "avg_loss": round(mean(losses), 2) if losses else None,
        "expectancy": round(mean(pnl), 2) if n else None,
        "median_pnl": round(median(pnl), 2) if n else None,
        "expectancy_ci": bootstrap_mean_ci(pnl, ci_level) if n else None,
        "expectancy_ci_level": ci_level,
        "avg_r": round(mean(rs), 4) if rs else None,
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "no_losing_trades": n > 0 and gl == 0,
        "gross_profit": round(gp, 2),
        "gross_loss": round(-gl, 2),
        "net_pnl": round(sum(pnl), 2),
        "max_drawdown": round(dd, 2),
        "max_consecutive_losses": _streak(trades, True),
        "max_consecutive_wins": _streak(trades, False),
        "sessions": sessions,
        "trades_per_session": round(n / sessions, 3) if sessions else None,
        "sessions_traded": len(daily),
        "profitable_sessions_pct": round(sum(1 for v in daily.values() if v > 0) / len(daily), 4) if daily else None,
        "avg_daily_pnl": round(sum(daily.values()) / sessions, 2) if sessions else None,
        "costs_usd": round(costs, 2),
        "costs_pct_of_gross_profit": round(costs / gross_ideal, 4) if gross_ideal > 0 else None,
        "monthly": {m: {"trades": len(v), "net": round(sum(v), 2)} for m, v in sorted(monthly.items())},
        "profitable_months_pct": round(sum(1 for v in monthly.values() if sum(v) > 0) / len(monthly), 4)
        if monthly
        else None,
        "sharpe_daily": _sharpe(list(daily.values())),
    }
    out["equity_curve"] = curve[-500:] if len(curve) > 500 else curve
    return out


def _sharpe(daily: Sequence[float]) -> float | None:
    if len(daily) < 5:
        return None
    m, sd = mean(daily), (sum((x - mean(daily)) ** 2 for x in daily) / (len(daily) - 1)) ** 0.5
    return round(m / sd * math.sqrt(252), 3) if sd > 0 else None


def breakdown(trades: Iterable[Any], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for t in trades:
        groups[str(getattr(t, key))].append(t)
    out = {}
    for k, ts in sorted(groups.items()):
        s = trade_stats(ts)
        out[k] = {x: s[x] for x in ("trades", "win_rate", "expectancy", "net_pnl", "profit_factor")}
    return out
