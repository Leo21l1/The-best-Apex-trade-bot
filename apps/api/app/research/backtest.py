"""Event-driven backtesting engine.

The engine replays stored 1-minute bars and evaluates the SAME strategy code the live scanner runs.
At each 5-minute close it builds a ``MarketContext`` containing only bars that had closed by then
(no look-ahead); orders are filled on later 1-minute bars with deliberately conservative rules:

* entry: market order at the open of the next 1-minute bar, plus adverse slippage;
* stop: triggered when a bar trades at or through it; filled at the stop or, if the bar opened
  beyond it (gap), at that bar's open; plus adverse slippage;
* target: a limit order filled only if price trades THROUGH it by at least one tick (touching is
  not enough), no slippage;
* stop and target inside the same bar: the stop is assumed to fill first;
* time exit: at the open of the first bar at or after 15:55 ET, or at the last close before a data
  gap / session end, plus slippage;
* one position at a time per run; commission charged per side per contract.

Everything is deterministic: the same bars and parameters always produce the same result, and the
result records a fingerprint of the dataset it ran on.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import itertools
import json
import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from typing import Any

from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate
from app.research.indicators import OPENING_RANGE_MINUTES, Levels, is_rth
from app.research.validation import STANDARDS, ValidationStandards
from app.strategies.base import MarketContext, Strategy
from app.strategies.library import STRATEGY_BY_ID

ENGINE_VERSION = "1.0"
EVAL_START = time(9, 35)
EVAL_END = time(15, 30)
FLAT_BY = time(15, 55)
LOOKBACK = timedelta(hours=120)  # same window the live scanner uses


def net_rr(
    params: BacktestParams, symbol: str, direction: str, entry: float, stop: float, target: float
) -> float | None:
    """Net R:R with the confirmation's cost model and this run's commission and slippage."""
    from app.services.checklist import ChecklistConfig, reward_risk

    cfg = ChecklistConfig(
        commission_per_side={"standard": params.commission_per_side, "micro": params.commission_per_side},
        slippage_ticks_per_side=params.slippage_ticks,
    )
    r = reward_risk(symbol, direction, entry, stop, target, cfg)
    return r["ratio"] if r["valid"] else None


@dataclass(frozen=True)
class BacktestParams:
    strategy_id: str
    symbol: str
    quantity: int = 1
    commission_per_side: float = 2.50
    slippage_ticks: float = 1.0
    #: Optional override of the strategy's (target-1, target-2) R multiples; target-1 is used for exits.
    target_r: tuple[float, float] | None = None
    #: Optional stop override: entry reference minus/plus ``stop_atr`` x ATR(14, 5m) at the signal.
    #: The target is then ``target_r[0]`` (or the strategy's first R multiple) times that risk.
    stop_atr: float | None = None
    #: Skip signals whose NET reward:risk to the exit target (same cost model as the live confirmation)
    #: is below this. None = take every signal as the strategy designed it.
    min_net_rr: float | None = None
    #: Which of the signal's targets the trade exits at (1 = T1, 2 = T2; falls back to T1 if there is no T2).
    exit_target: int = 1
    #: Stress tests: extra adverse slippage on stop fills only, and entering N bars late.
    stop_slippage_extra_ticks: float = 0.0
    entry_delay_bars: int = 0
    #: Risk Guardian consistency: skip signals whose 1-contract loss at the stop (signal levels, round-trip
    #: commission and slippage) exceeds this - the live Risk Guardian would size them at 0 contracts.
    max_risk_usd: float | None = None
    #: Share of trading days (from the end) held out as out-of-sample.
    oos_fraction: float = 0.3
    walk_forward_folds: int = 4
    monte_carlo_runs: int = 1000
    seed: int = 7


@dataclass
class Trade:
    direction: str
    signal_ts: str
    entry_ts: str
    exit_ts: str
    signal_entry: float
    entry: float
    stop: float
    target: float
    exit: float
    exit_reason: str  # stop | target | time | data_end
    quantity: int
    pnl: float  # after commission
    commission: float
    r: float
    trading_day: str
    bars_held: int
    # Context at the signal (computed only from bars closed by then).
    session: str = ""
    weekday: str = ""
    hour_et: int = 0
    regime: str = "unknown"
    stop_ticks: float = 0.0
    planned_rr: float | None = None
    rvol: float | None = None
    # Audit fields (USD, all contracts): price P&L at ideal fills, slippage cost, initial risk,
    # max favourable / adverse excursion on 1-minute highs/lows from the entry bar to the exit bar.
    symbol: str = ""
    strategy_id: str = ""
    contract: str = ""
    gross_pnl: float = 0.0
    slippage_usd: float = 0.0
    risk_usd: float = 0.0
    mfe_usd: float = 0.0
    mae_usd: float = 0.0


class _ReplayContext(MarketContext):
    """MarketContext backed by precomputed, prefix-sliced series (identical rules, much faster)."""


def _fingerprint(bars: Sequence[Bar]) -> dict[str, Any]:
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b.ts.isoformat()}|{b.open}|{b.high}|{b.low}|{b.close}|{b.volume};".encode())
    return {
        "bars": len(bars),
        "first": bars[0].ts.isoformat() if bars else None,
        "last": bars[-1].ts.isoformat() if bars else None,
        "sha256": h.hexdigest(),
    }


class Replayer:
    """Holds precomputed series so each evaluation step is cheap."""

    def __init__(self, symbol: str, bars: Sequence[Bar]) -> None:
        self.symbol = symbol
        self.bars = list(bars)
        self.times = [b.ts for b in self.bars]
        self.series = {tf: aggregate(self.bars, tf) for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1)}
        self.series_times = {tf: [b.ts for b in s] for tf, s in self.series.items()}
        # Per-bar calendar facts, computed once (the live code derives them per call).
        self.day = [trading_day(t) for t in self.times]
        self.local = [t.astimezone(ET) for t in self.times]
        self.rth = [lt.weekday() < 5 and time(9, 30) <= lt.time() < time(16, 0) for lt in self.local]
        self.day_range: dict[Any, tuple[int, int]] = {}
        for idx, d in enumerate(self.day):
            first, _ = self.day_range.get(d, (idx, idx))
            self.day_range[d] = (first, idx)
        self.days_sorted = sorted(self.day_range)

    def levels(self, now: datetime, lo: int, hi: int) -> Levels:
        """Same result as ``reference_levels(self.bars[lo:hi], now)``, using precomputed day ranges."""
        today = trading_day(now)
        pdh = pdl = pdc = onh = onl = orh = orl = None
        k = bisect.bisect_left(self.days_sorted, today) - 1
        while k >= 0:
            d = self.days_sorted[k]
            a, b = self.day_range[d]
            a, b = max(a, lo), min(b, hi - 1)
            rth = [i for i in range(a, b + 1) if self.rth[i]]
            if rth:
                pdh = max(self.bars[i].high for i in rth)
                pdl = min(self.bars[i].low for i in rth)
                pdc = self.bars[rth[-1]].close
                break
            if b < lo:
                break
            k -= 1
        or_end = None
        if today in self.day_range:
            a, b = self.day_range[today]
            a, b = max(a, lo), min(b, hi - 1)
            for i in range(a, b + 1):
                bar = self.bars[i]
                lt = self.local[i].time()
                if not self.rth[i] and (lt >= time(18, 0) or lt < time(9, 30)):
                    onh = bar.high if onh is None else max(onh, bar.high)
                    onl = bar.low if onl is None else min(onl, bar.low)
                elif self.rth[i]:
                    start = self.local[i].replace(hour=9, minute=30, second=0, microsecond=0)
                    or_end = start + timedelta(minutes=OPENING_RANGE_MINUTES)
                    if bar.ts < or_end:
                        orh = bar.high if orh is None else max(orh, bar.high)
                        orl = bar.low if orl is None else min(orl, bar.low)
        complete = or_end is not None and now >= or_end and orh is not None
        return Levels(pdh, pdl, pdc, onh, onl, orh, orl, complete)

    def context(self, now: datetime) -> MarketContext:
        lo = bisect.bisect_left(self.times, now - LOOKBACK)
        hi = bisect.bisect_left(self.times, now)  # bars that opened before `now` (all closed)
        ctx = _ReplayContext(self.symbol, now, self.bars[lo:hi])
        ctx.__dict__["levels"] = self.levels(now, lo, hi)  # pre-fill the cached property
        start = now - LOOKBACK
        for tf, series in self.series.items():
            ts = self.series_times[tf]
            a = bisect.bisect_left(ts, start)
            b = bisect.bisect_right(ts, now - tf.delta)  # only buckets fully closed by `now`
            ctx._cache[tf] = series[a:b]
        return ctx


def _round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 6)


def _window_key(ts: datetime) -> str:
    from app.services.session_intel import reference_window

    return reference_window(ts)["key"]


def _regime_label(closes: Sequence[float]) -> str:
    """Efficiency ratio of recent 5m closes: >= 0.35 trending, < 0.15 choppy, otherwise ranging."""
    if len(closes) < 6:
        return "unknown"
    path = sum(abs(b - a) for a, b in itertools.pairwise(closes))
    if path <= 0:
        return "flat"
    er = abs(closes[-1] - closes[0]) / path
    trend = "up" if closes[-1] > closes[0] else "down"
    return f"trending {trend}" if er >= 0.35 else "choppy" if er < 0.15 else "ranging"


def attach_rvol(trades: list[Trade], bars: Sequence[Bar], lookback: int = 20, min_sessions: int = 5) -> None:
    """Relative volume of the signal's 5-minute bar vs the SAME New York clock bucket on up to
    ``lookback`` PRIOR sessions (median). Fewer than ``min_sessions`` comparable sessions -> None."""
    by: dict[tuple[str, str], int] = {}
    for b in bars:
        loc = b.ts.astimezone(ET)
        bucket = f"{loc.hour:02d}:{loc.minute - loc.minute % 5:02d}"
        key = (trading_day(b.ts).isoformat(), bucket)
        by[key] = by.get(key, 0) + b.volume
    days = sorted({d for d, _ in by})
    for t in trades:
        sig = datetime.fromisoformat(t.signal_ts) - timedelta(minutes=5)
        loc = sig.astimezone(ET)
        bucket = f"{loc.hour:02d}:{loc.minute - loc.minute % 5:02d}"
        d = trading_day(sig).isoformat()
        cur = by.get((d, bucket))
        prior = [by[(x, bucket)] for x in days if x < d and (x, bucket) in by][-lookback:]
        if cur is None or len(prior) < min_sessions:
            continue
        from statistics import median

        base = median(prior)
        t.rvol = round(cur / base, 2) if base > 0 else None


def _bucket_rr(x: float | None) -> str:
    return (
        "unknown" if x is None else "< 1.0" if x < 1 else "1.0-1.5" if x < 1.5 else "1.5-2.5" if x < 2.5 else ">= 2.5"
    )


def _bucket_ticks(x: float) -> str:
    return "<= 8 ticks" if x <= 8 else "8-16 ticks" if x <= 16 else "16-32 ticks" if x <= 32 else "> 32 ticks"


def _bucket_rvol(x: float | None) -> str:
    return (
        "unknown (< 5 sessions)" if x is None else "low (< 0.8)" if x < 0.8 else "high (> 1.2)" if x > 1.2 else "normal"
    )


SEGMENT_DIMENSIONS: dict[str, Any] = {
    "session": lambda t: t.session or "unknown",
    "weekday": lambda t: t.weekday or "unknown",
    "hour_et": lambda t: f"{t.hour_et:02d}:00",
    "regime": lambda t: t.regime,
    "direction": lambda t: t.direction,
    "relative_volume": lambda t: _bucket_rvol(t.rvol),
    "stop_distance": lambda t: _bucket_ticks(t.stop_ticks),
    "planned_reward_risk": lambda t: _bucket_rr(t.planned_rr),
    "exit_reason": lambda t: t.exit_reason,
}


def segments(trades: Sequence[Trade]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for name, key in SEGMENT_DIMENSIONS.items():
        groups: dict[str, list[Trade]] = {}
        for t in trades:
            groups.setdefault(key(t), []).append(t)
        rows = []
        for k, g in sorted(groups.items()):
            m = metrics(g, len({t.trading_day for t in g}), 0)
            rows.append(
                {
                    "key": k,
                    **_brief(m),
                    "avg_win": m["avg_win"],
                    "avg_loss": m["avg_loss"],
                    "expectancy": m["expectancy"],
                    "small_sample": len(g) < 30,
                }
            )
        out[name] = rows
    return out


def _strategy(params: BacktestParams) -> Strategy:
    base = STRATEGY_BY_ID[params.strategy_id]
    if params.target_r is None:
        return base
    s = copy.copy(base)
    s.target_r = params.target_r
    return s


@dataclass
class SignalEvent:
    """A strategy signal at a completed 5-minute close, with the context needed to execute it."""

    close_ts: datetime
    signal: Any
    atr: float | None
    regime: str


def generate_signals(
    params: BacktestParams, bars: Sequence[Bar], stats: Counter[str] | None = None
) -> tuple[Replayer, list[SignalEvent]]:
    """Evaluate the strategy at every completed 5-minute close in the evaluation window.

    Signals do not depend on the exit configuration, so they are generated once and executed under
    many configurations and stress scenarios. Uses only bars that opened before each close."""
    st: Counter[str] = stats if stats is not None else Counter()
    strategy = _strategy(params)
    rp = Replayer(params.symbol, bars)
    events: list[SignalEvent] = []
    for close_ts in (b.ts + Timeframe.M5.delta for b in rp.series[Timeframe.M5]):
        local = close_ts.astimezone(ET).time()
        st["5m bars"] += 1
        if not (EVAL_START <= local <= EVAL_END):
            st["outside evaluation window (09:35-15:30 ET)"] += 1
            continue
        st["evaluated"] += 1
        ctx = rp.context(close_ts)
        ev = strategy.evaluate(ctx)
        if ev.signal is None:
            if ev.note:
                key = "missing history" if "insufficient history" in ev.note else ev.note
                st[f"no signal: {key}"] += 1
            else:
                failed = next((c.name for c in ev.conditions if not c.passed), "conditions not met")
                st[f"no signal: condition '{failed}' failed"] += 1
            continue
        st["signals"] += 1
        events.append(
            SignalEvent(close_ts, ev.signal, ctx.atr14[-1] if ctx.atr14 else None, _regime_label(ctx.closes[-24:]))
        )
    return rp, events


def execute(
    params: BacktestParams,
    rp: Replayer,
    events: Sequence[SignalEvent],
    stats: Counter[str] | None = None,
    contract: str = "",
) -> list[Trade]:
    """Turn signals into trades: one position at a time, conservative fills (see execution_policy)."""
    st: Counter[str] = stats if stats is not None else Counter()
    inst = INSTRUMENTS[params.symbol]
    tick = float(inst.tick_size)
    point_value = float(inst.point_value)
    slip = params.slippage_ticks * tick
    stop_slip = (params.slippage_ticks + params.stop_slippage_extra_ticks) * tick
    strategy = _strategy(params)
    trades: list[Trade] = []
    n = len(rp.bars)
    pos: dict[str, Any] | None = None
    for evn in events:
        close_ts, sig = evn.close_ts, evn.signal
        if pos is not None and close_ts <= pos["exit_after"]:
            st["signal skipped: position already open"] += 1
            continue
        i = bisect.bisect_left(rp.times, close_ts) + params.entry_delay_bars
        if i >= n or rp.times[i] - close_ts > timedelta(minutes=2 + params.entry_delay_bars):
            st["signal rejected: no next bar to fill on"] += 1
            continue  # no next bar to fill on (session end / data gap)
        s = 1 if sig.direction == "long" else -1
        entry_bar = rp.bars[i]
        entry = entry_bar.open + s * slip
        stop = sig.stop
        target = sig.targets[1] if params.exit_target == 2 and len(sig.targets) > 1 else sig.targets[0]
        if params.stop_atr:
            a = evn.atr
            if not a:
                st["signal rejected: no ATR for the stop override"] += 1
                continue  # no ATR yet: the override cannot be applied honestly
            stop = _round_tick(sig.entry - s * params.stop_atr * a, tick)
            rr1 = (params.target_r or strategy.target_r)[0]
            target = _round_tick(sig.entry + s * rr1 * abs(sig.entry - stop), tick)
        if (entry - stop) * s <= 0 or (target - entry) * s <= 0:
            st["signal rejected: price gapped beyond stop or target before entry"] += 1
            continue  # gapped beyond stop or target before entry: no trade
        if params.min_net_rr is not None:
            # Gate on the levels known at the signal (what the confirmation sees), not on the later fill.
            nrr = net_rr(params, sig.symbol, sig.direction, sig.entry, stop, target)
            if nrr is None or nrr < params.min_net_rr:
                st[f"signal rejected: net R:R below {params.min_net_rr:g}"] += 1
                continue
        if params.max_risk_usd is not None:
            loss_1c = (
                abs(sig.entry - stop) * point_value
                + 2 * params.commission_per_side
                + 2 * params.slippage_ticks * tick * point_value
            )
            if loss_1c > params.max_risk_usd:
                st[
                    f"signal rejected: 1-contract loss at stop above the ${params.max_risk_usd:,.0f} risk budget "
                    "(Risk Guardian would size 0)"
                ] += 1
                continue
        exit_price = exit_reason = None
        j = i
        while j < n:
            b = rp.bars[j]
            if j > i and b.ts - rp.bars[j - 1].ts > timedelta(minutes=2):
                exit_price, exit_reason, j = rp.bars[j - 1].close - s * slip, "data_end", j - 1
                break
            if b.ts.astimezone(ET).time() >= FLAT_BY:
                exit_price, exit_reason = b.open - s * slip, "time"
                break
            hit_stop = b.low <= stop if s == 1 else b.high >= stop
            hit_target = b.high >= target + tick if s == 1 else b.low <= target - tick
            if hit_stop:  # checked first: a bar touching both is a loss (no intrabar order known)
                gap = b.open < stop if s == 1 else b.open > stop
                exit_price, exit_reason = (b.open if gap else stop) - s * stop_slip, "stop"
                break
            if hit_target:
                exit_price, exit_reason = target, "target"
                break
            j += 1
        if exit_price is None:
            j = n - 1
            exit_price, exit_reason = rp.bars[j].close - s * slip, "data_end"
        q = params.quantity
        commission = 2 * params.commission_per_side * q
        risk = abs(entry - stop)
        pnl = (exit_price - entry) * s * point_value * q - commission
        exit_slip_ticks = (
            0.0
            if exit_reason == "target"
            else (params.slippage_ticks + (params.stop_slippage_extra_ticks if exit_reason == "stop" else 0.0))
        )
        slippage_usd = (params.slippage_ticks + exit_slip_ticks) * tick * point_value * q
        held = rp.bars[i : j + 1]
        if s == 1:
            mfe = max(b.high for b in held) - entry
            mae = entry - min(b.low for b in held)
        else:
            mfe = entry - min(b.low for b in held)
            mae = max(b.high for b in held) - entry
        st["trades"] += 1
        trades.append(
            Trade(
                direction=sig.direction,
                signal_ts=close_ts.isoformat(),
                entry_ts=entry_bar.ts.isoformat(),
                exit_ts=rp.bars[j].ts.isoformat(),
                signal_entry=sig.entry,
                entry=round(entry, 6),
                stop=stop,
                target=target,
                exit=round(exit_price, 6),
                exit_reason=exit_reason,
                quantity=q,
                pnl=round(pnl, 2),
                commission=round(commission, 2),
                r=round(pnl / (risk * point_value * q), 4) if risk > 0 else 0.0,
                trading_day=trading_day(entry_bar.ts).isoformat(),
                bars_held=j - i + 1,
                session=_window_key(close_ts),
                weekday=close_ts.astimezone(ET).strftime("%a"),
                hour_et=close_ts.astimezone(ET).hour,
                regime=evn.regime,
                stop_ticks=round(abs(entry - stop) / tick, 1),
                planned_rr=round(abs(target - entry) / abs(entry - stop), 2) if entry != stop else None,
                symbol=params.symbol,
                strategy_id=params.strategy_id,
                contract=contract,
                gross_pnl=round(pnl + commission + slippage_usd, 2),
                slippage_usd=round(slippage_usd, 2),
                risk_usd=round(risk * point_value * q, 2),
                mfe_usd=round(max(mfe, 0.0) * point_value * q, 2),
                mae_usd=round(max(mae, 0.0) * point_value * q, 2),
            )
        )
        pos = {"exit_after": rp.bars[j].ts}
    return trades


def simulate(params: BacktestParams, bars: Sequence[Bar], stats: Counter[str] | None = None) -> list[Trade]:
    """Run one configuration over ascending 1-minute bars and return the trades.

    ``stats`` (optional) counts every completed 5-minute bar by outcome (outside the evaluation
    window, missing history, the first strategy condition that failed) and every reason a signal
    did not become a trade (position already open, no bar to fill on, gap, net R:R gate)."""
    st: Counter[str] = stats if stats is not None else Counter()
    rp, events = generate_signals(params, bars, st)
    return execute(params, rp, events, st)


# --- metrics ---------------------------------------------------------------------------------


def metrics(trades: Sequence[Trade], trading_days: int, total_minutes: int) -> dict[str, Any]:
    n = len(trades)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    equity = peak = max_dd = 0.0
    for t in trades:
        equity += t.pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    r_values = [t.r for t in trades]
    mean_r = sum(r_values) / n if n else None
    sd_r = math.sqrt(sum((r - mean_r) ** 2 for r in r_values) / (n - 1)) if n > 1 and mean_r is not None else None
    r_equity = r_peak = max_dd_r = 0.0
    for r in r_values:
        r_equity += r
        r_peak = max(r_peak, r_equity)
        max_dd_r = max(max_dd_r, r_peak - r_equity)
    daily: dict[str, float] = {}
    for t in trades:
        daily[t.trading_day] = daily.get(t.trading_day, 0.0) + t.pnl
    d = list(daily.values()) + [0.0] * max(trading_days - len(daily), 0)
    d_mean = sum(d) / len(d) if d else 0.0
    d_sd = math.sqrt(sum((x - d_mean) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else 0.0
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        # No losing trades -> profit factor undefined (was math.inf, which PostgreSQL JSON rejects).
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "no_losing_trades": bool(wins) and gross_loss == 0,
        "expectancy": round(sum(t.pnl for t in trades) / n, 2) if n else None,
        "expectancy_r": round(mean_r, 4) if mean_r is not None else None,
        "t_stat_r": round(mean_r / (sd_r / math.sqrt(n)), 3) if sd_r else None,
        "net_profit": round(sum(t.pnl for t in trades), 2),
        "total_commission": round(sum(t.commission for t in trades), 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_r": round(max_dd_r, 3),
        "trades_per_day": round(n / trading_days, 3) if trading_days else None,
        "exposure": round(sum(t.bars_held for t in trades) / total_minutes, 4) if total_minutes else None,
        "daily_sharpe_annualized": round(d_mean / d_sd * math.sqrt(252), 3) if d_sd > 0 else None,
        "avg_hold_minutes": round(sum(t.bars_held for t in trades) / n, 1) if n else None,
        "max_consecutive_wins": _streak(trades, True),
        "max_consecutive_losses": _streak(trades, False),
        "exit_reasons": {
            k: sum(1 for t in trades if t.exit_reason == k) for k in ("stop", "target", "time", "data_end")
        },
    }


def _streak(trades: Sequence[Trade], wins: bool) -> int:
    best = cur = 0
    for t in trades:
        cur = cur + 1 if (t.pnl > 0) == wins else 0
        best = max(best, cur)
    return best


def monte_carlo(trades: Sequence[Trade], runs: int, seed: int) -> dict[str, Any] | None:
    """Bootstrap the trade P&L sequence: distribution of net profit and max drawdown."""
    if len(trades) < 10:
        return None
    rng = random.Random(seed)  # noqa: S311 - seeded, reproducible resampling; not security-related
    pnl = [t.pnl for t in trades]
    finals, dds = [], []
    for _ in range(runs):
        sample = [pnl[rng.randrange(len(pnl))] for _ in pnl]
        eq = pk = dd = 0.0
        for x in sample:
            eq += x
            pk = max(pk, eq)
            dd = max(dd, pk - eq)
        finals.append(eq)
        dds.append(dd)
    finals.sort()
    dds.sort()

    def pct(a: list[float], p: float) -> float:
        return round(a[min(len(a) - 1, int(p * len(a)))], 2)

    return {
        "runs": runs,
        "seed": seed,
        "net_profit": {"p5": pct(finals, 0.05), "p50": pct(finals, 0.5), "p95": pct(finals, 0.95)},
        "max_drawdown": {"p50": pct(dds, 0.5), "p95": pct(dds, 0.95), "p99": pct(dds, 0.99)},
        "probability_of_loss": round(sum(1 for f in finals if f < 0) / runs, 4),
    }


def _days(bars: Sequence[Bar]) -> list[str]:
    """Trading days that can contain a trade: those with regular-session (09:30-16:00 ET) bars.

    Strategies only enter between 09:35 and 15:30 ET. Counting a day that holds only overnight bars
    (e.g. the session that opened at 18:00 ET last night) made it the whole out-of-sample period on
    short histories, so the out-of-sample test could never contain a trade.
    """
    return sorted({trading_day(b.ts).isoformat() for b in bars if is_rth(b.ts)})


def evaluate_validation(
    in_sample: dict[str, Any],
    oos: dict[str, Any],
    folds: list[dict[str, Any]],
    full: dict[str, Any],
    quality: str,
    standards: ValidationStandards = STANDARDS,
) -> dict[str, Any]:
    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    check(
        "real historical data",
        quality in ("historical", "live", "delayed"),
        f"dataset class is {quality}; simulated data can never validate a strategy",
    )
    check(f"at least {standards.min_trades} trades", full["trades"] >= standards.min_trades, f"{full['trades']} trades")
    check(
        f"at least {standards.min_out_of_sample_trades} out-of-sample trades",
        oos["trades"] >= standards.min_out_of_sample_trades,
        f"{oos['trades']} out-of-sample trades",
    )
    pf = oos["profit_factor"]
    check(
        f"out-of-sample profit factor >= {standards.min_profit_factor}",
        pf is not None and pf >= standards.min_profit_factor,
        f"{pf}",
    )
    er = oos["expectancy_r"]
    check(
        f"out-of-sample expectancy >= {standards.min_expectancy_r}R",
        er is not None and er >= standards.min_expectancy_r,
        f"{er}R",
    )
    check(
        f"max drawdown <= {standards.max_drawdown_r}R",
        full["max_drawdown_r"] <= standards.max_drawdown_r,
        f"{full['max_drawdown_r']}R",
    )
    profitable = sum(1 for f in folds if (f["metrics"]["net_profit"] or 0) > 0)
    need = math.ceil(0.6 * len(folds)) if folds else 1
    check(
        "walk-forward: profitable in >= 60% of folds",
        standards.require_walk_forward is False or (folds and profitable >= need),
        f"{profitable} of {len(folds)} folds profitable",
    )
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def run_backtest(
    params: BacktestParams,
    bars: Sequence[Bar],
    quality: str,
    prior_configs: int = 0,
    sensitivity: bool = True,
    stats: Counter[str] | None = None,
) -> dict[str, Any]:
    """Full experiment: base run, in-sample / out-of-sample split, walk-forward folds, Monte Carlo,
    parameter sensitivity and validation against the research standards."""
    if params.strategy_id not in STRATEGY_BY_ID:
        raise ValueError("unknown strategy")
    if params.symbol not in STRATEGY_BY_ID[params.strategy_id].spec.eligible_instruments:
        raise ValueError(f"{params.symbol} is not eligible for {params.strategy_id}")
    bars = list(bars)
    days = _days(bars)
    trades = simulate(params, bars, stats)
    attach_rvol(trades, bars)
    total_minutes = len(bars)
    full = metrics(trades, len(days), total_minutes)

    split = int(len(days) * (1 - params.oos_fraction))
    # At least one session on each side when possible; a single session is all in-sample (no OOS).
    split = min(max(split, 1), len(days) - 1) if len(days) >= 2 else len(days)
    is_days, oos_days = set(days[:split]), set(days[split:])
    in_sample = metrics([t for t in trades if t.trading_day in is_days], len(is_days), total_minutes)
    oos = metrics([t for t in trades if t.trading_day in oos_days], len(oos_days), total_minutes)

    folds = []
    k = max(1, min(params.walk_forward_folds, len(days)))
    size = math.ceil(len(days) / k) if days else 0
    for f in range(k):
        fd = set(days[f * size : (f + 1) * size])
        if not fd:
            continue
        folds.append(
            {
                "fold": f + 1,
                "first_day": min(fd),
                "last_day": max(fd),
                "metrics": metrics([t for t in trades if t.trading_day in fd], len(fd), total_minutes),
            }
        )

    sens: list[dict[str, Any]] = []
    configs = 1
    if sensitivity and trades:
        base_targets = STRATEGY_BY_ID[params.strategy_id].target_r
        for tr in (1.0, 2.0, 3.0):
            if tr == (params.target_r or base_targets)[0]:
                continue
            p = BacktestParams(**{**asdict(params), "target_r": (tr, tr + 1)})
            sens.append({"variant": f"target {tr}R", **_brief(metrics(simulate(p, bars), len(days), total_minutes))})
            configs += 1
        for sl in (0.0, 2.0):
            if sl == params.slippage_ticks:
                continue
            p = BacktestParams(**{**asdict(params), "slippage_ticks": sl})
            sens.append(
                {"variant": f"slippage {sl:g} tick", **_brief(metrics(simulate(p, bars), len(days), total_minutes))}
            )
            configs += 1

    tested = prior_configs + configs
    t_stat = full["t_stat_r"]
    # Bonferroni: two-sided 5% significance across every configuration tried for this strategy.
    from statistics import NormalDist

    z_needed = NormalDist().inv_cdf(1 - 0.05 / (2 * max(tested, 1)))
    validation = evaluate_validation(in_sample, oos, folds, full, quality)
    result = {
        "engine_version": ENGINE_VERSION,
        "params": asdict(params),
        "dataset": {**_fingerprint(bars), "quality": quality, "trading_days": len(days)},
        "metrics": full,
        "in_sample": in_sample,
        "out_of_sample": oos,
        "split": {
            "in_sample_days": len(is_days),
            "out_of_sample_days": len(oos_days),
            "first_oos_day": min(oos_days) if oos_days else None,
            "in_sample_bars": sum(1 for b in bars if trading_day(b.ts).isoformat() in is_days),
            "out_of_sample_bars": sum(1 for b in bars if trading_day(b.ts).isoformat() in oos_days),
            "rule": "chronological: the last oos_fraction of trading days with regular-session bars",
        },
        "walk_forward": folds,
        "monte_carlo": monte_carlo(trades, params.monte_carlo_runs, params.seed),
        "sensitivity": sens,
        "multiple_testing": {
            "configurations_tested": tested,
            "t_stat_r": t_stat,
            "z_required_bonferroni": round(z_needed, 3),
            "significant_after_adjustment": t_stat is not None and t_stat >= z_needed,
            "note": "Counts every configuration run for this strategy (this and earlier experiments).",
        },
        "validation": validation,
        "assumptions": [
            "Market entry at next 1-minute bar open plus slippage",
            "Targets need a one-tick trade-through; stop assumed first when both are hit in one bar",
            "Gaps through the stop fill at the bar open",
            f"Flat by {FLAT_BY:%H:%M} ET; one position at a time; {params.quantity} contract(s)",
            "Indicators use a precomputed 120-hour window; the first bucket of each window may differ "
            "slightly from the live scanner's",
            "Continuous front-month bars; no roll, spread or queue modelling beyond slippage",
        ],
        "trades": [asdict(t) for t in trades],
        "segments": {
            "in_sample": segments([t for t in trades if t.trading_day in is_days]),
            "out_of_sample": segments([t for t in trades if t.trading_day in oos_days]),
            "note": "Segments with fewer than 30 trades are flagged small_sample; in-sample results are optimistic "
            "by construction. Past performance does not predict future results.",
        },
        "execution_policy": {
            "entry": "market at the next 1-minute bar open after the signal bar closes, plus slippage",
            "stop": "triggered when a bar trades at/through it; fills at the stop or a gapped open, plus slippage",
            "target": "limit filled only when price trades one tick THROUGH it, no slippage",
            "both_in_one_bar": "stop assumed first (conservative)",
            "time_exit": f"flat at the first bar at/after {FLAT_BY:%H:%M} ET or before a data gap",
        },
    }
    result = json_safe(result)
    result["result_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in result.items() if k != "result_sha256"}, sort_keys=True, default=str).encode()
    ).hexdigest()
    return result


def json_safe(x: Any) -> Any:
    """Replace non-finite floats (inf / nan) with None so results are valid JSON for PostgreSQL."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_safe(v) for v in x]
    return x


def _brief(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m[k] for k in ("trades", "win_rate", "profit_factor", "expectancy_r", "net_profit", "max_drawdown")}
