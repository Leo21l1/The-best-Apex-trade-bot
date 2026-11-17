"""Trade review: context, news attribution, automatic journal, coaching findings, scenarios, replay.

Everything here is computed deterministically from stored records (trades, 1-minute bars, signals,
calendar events, plans). It never diagnoses emotions and never claims causation: news is described
as an observed correlation with measured price behaviour. Hypothetical scenarios are labelled as
such and use the same conservative fill rules and costs as the backtester.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.marketdata.calendar import ET
from app.marketdata.instruments import INSTRUMENTS
from app.models.calendar import EconomicEvent
from app.models.research import JournalTrade, TradePlan
from app.models.signal import SignalRow
from app.research.bars import Bar
from app.research.indicators import atr
from app.research.radar import regime
from app.research.scanner import load_bars
from app.strategies.base import MarketContext

REVIEW_VERSION = "1.0"
CAUSATION_NOTE = (
    "Observed timing only. Proximity between an event and a price move or trade outcome is not evidence of causation."
)
NEWS_LOOKBACK = timedelta(hours=24)
FLAT_BY = time(15, 55)


@dataclass
class TradeData:
    trade: JournalTrade
    plan: TradePlan | None
    bars: list[Bar]  # 1-minute bars from entry-120h to exit+8h (enough to finish the session)
    events: list[EconomicEvent]
    signal: SignalRow | None
    day_trades: list[JournalTrade]
    provider: str


def _tv(symbol: str) -> tuple[float, float]:
    inst = INSTRUMENTS[symbol]
    return float(inst.tick_size), float(inst.point_value)


async def load_trade_data(session: AsyncSession, trade: JournalTrade, settings: Settings) -> TradeData:
    provider = settings.market_data_provider
    bars = (
        await load_bars(
            session,
            provider,
            trade.symbol,
            trade.entry_time - timedelta(hours=120),
            trade.exit_time + timedelta(hours=8),
        )
        if provider != "none"
        else []
    )
    events = list(
        (
            await session.scalars(
                select(EconomicEvent)
                .where(
                    EconomicEvent.ts >= trade.entry_time - NEWS_LOOKBACK,
                    EconomicEvent.ts <= trade.exit_time + timedelta(hours=2),
                )
                .order_by(EconomicEvent.ts)
            )
        ).all()
    )
    plan = await session.get(TradePlan, trade.plan_id) if trade.plan_id else None
    signal = await session.scalar(
        select(SignalRow)
        .where(
            SignalRow.symbol == trade.symbol,
            SignalRow.direction == trade.direction,
            SignalRow.created_at <= trade.entry_time,
            SignalRow.created_at >= trade.entry_time - timedelta(minutes=30),
        )
        .order_by(SignalRow.created_at.desc())
        .limit(1)
    )
    day_trades = list(
        (
            await session.scalars(
                select(JournalTrade)
                .where(JournalTrade.trading_date == trade.trading_date)
                .order_by(JournalTrade.entry_time)
            )
        ).all()
    )
    return TradeData(trade, plan, bars, events, signal, day_trades, provider)


def _stop_target(d: TradeData) -> tuple[float | None, float | None]:
    t = d.trade
    stop = t.planned_stop if t.planned_stop is not None else (d.plan.stop if d.plan else None)
    target = t.planned_target if t.planned_target is not None else (d.plan.target if d.plan else None)
    return stop, target


def excursions(d: TradeData) -> tuple[float | None, float | None, list[Bar]]:
    """(MFE $, MAE $, bars during the trade). Bars overlapping the holding period are used."""
    t = d.trade
    start = t.entry_time.replace(second=0, microsecond=0)
    held = [b for b in d.bars if start <= b.ts < t.exit_time]
    if not held:
        return None, None, []
    _, pv = _tv(t.symbol)
    s = 1 if t.direction == "long" else -1
    best = max((b.high - t.entry_price) * s if s == 1 else (t.entry_price - b.low) for b in held)
    worst = max((t.entry_price - b.low) if s == 1 else (b.high - t.entry_price) for b in held)
    q = t.quantity
    return round(max(best, 0) * pv * q, 2), round(max(worst, 0) * pv * q, 2), held


def market_context(d: TradeData) -> dict[str, Any]:
    t = d.trade
    before = [b for b in d.bars if b.ts + timedelta(minutes=1) <= t.entry_time]
    if len(before) < 300:
        return {"available": False, "reason": "Not enough stored bars before entry to describe conditions."}
    ctx = MarketContext(t.symbol, t.entry_time, before)
    from app.marketdata.types import Timeframe

    trends = {tf.value: ctx.trend(tf) for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1)}
    label, why = regime(ctx)
    a_now = ctx.atr14[-1] if ctx.m5 else None
    hist = [x for x in atr(ctx.m5, 14)[-300:-1] if x is not None]
    median = sorted(hist)[len(hist) // 2] if hist else None
    ratio = round(a_now / median, 2) if a_now and median else None
    vw = ctx.vwap[0][-1] if ctx.m5 else None
    return {
        "available": True,
        "trend": {k: {1: "up", -1: "down", 0: "flat", None: "unknown"}[v] for k, v in trends.items()},
        "regime": label,
        "regime_definition": why,
        "atr_5m": round(a_now, 4) if a_now else None,
        "volatility_ratio": ratio,
        "volatility": None if ratio is None else ("elevated" if ratio >= 1.5 else "low" if ratio <= 0.67 else "normal"),
        "vwap": round(vw, 4) if vw else None,
        "vs_vwap": None if vw is None else ("above" if before[-1].close > vw else "below"),
        "levels": {k: v for k, v in ctx.levels.__dict__.items()} if hasattr(ctx.levels, "__dict__") else {},
    }


def _reaction(bars: list[Bar], times: list[datetime], ev_ts: datetime) -> dict[str, Any] | None:
    """Price behaviour in the 15 minutes after an event versus the hour before it."""
    i = bisect.bisect_left(times, ev_ts.replace(second=0, microsecond=0))
    if i >= len(bars) or i < 30 or bars[i].ts - ev_ts > timedelta(minutes=5):
        return None
    after = bars[i : i + 15]
    prior = bars[max(0, i - 60) : i]
    if len(after) < 5 or len(prior) < 30:
        return None
    p0, p1 = after[0].open, after[-1].close
    rng_after = max(b.high for b in after) - min(b.low for b in after)
    windows = [prior[k : k + 15] for k in range(0, len(prior) - 14, 15)]
    rng_before = sum(max(b.high for b in w) - min(b.low for b in w) for w in windows) / len(windows)
    ratio = rng_after / rng_before if rng_before > 0 else None
    change = p1 - p0
    direction = "flat" if rng_before and abs(change) < 0.5 * rng_before else ("up" if change > 0 else "down")
    return {
        "change": round(change, 4),
        "direction": direction,
        "range_15m": round(rng_after, 4),
        "range_ratio_vs_prior_hour": round(ratio, 2) if ratio is not None else None,
        "volatility_increased": bool(ratio and ratio >= 1.5),
    }


def news_attribution(d: TradeData) -> dict[str, Any]:
    t = d.trade
    times = [b.ts for b in d.bars]
    items = []
    for e in d.events:
        if t.symbol not in (e.affected or []):
            continue
        phase = "before_entry" if e.ts < t.entry_time else "while_open" if e.ts <= t.exit_time else "after_exit"
        if phase == "after_exit" and e.ts - t.exit_time > timedelta(minutes=30):
            continue
        r = _reaction(d.bars, times, e.ts)
        aligned = None
        if r and r["direction"] != "flat" and phase != "after_exit":
            aligned = (r["direction"] == "up") == (t.direction == "long")
        if r is None:
            wording = "Insufficient stored price data around this event to describe a reaction."
        elif r["direction"] == "flat":
            wording = "No clear directional reaction was observed in the 15 minutes after the event."
        else:
            wording = f"Price moved {r['direction']} in the 15 minutes after the event; " + (
                "the trade direction was consistent with this observed reaction."
                if aligned
                else "the trade direction was opposite to this observed reaction."
                if aligned is False
                else "the trade was not open around it."
            )
        items.append(
            {
                "title": e.title,
                "ts": e.ts.isoformat(),
                "importance": e.importance,
                "category": e.category,
                "phase": phase,
                "source": e.source,
                "source_ref": e.source_ref,
                "known_before_entry": e.created_at is not None and e.created_at <= t.entry_time,
                "reaction": r,
                "direction_aligned": aligned,
                "description": wording,
                "forecast": "No directional forecast was recorded for this event, so forecast confirmation "
                "cannot be assessed.",
            }
        )
    high_near = [
        i
        for i in items
        if i["importance"] == "high"
        and i["phase"] in ("before_entry", "while_open")
        and abs((datetime.fromisoformat(i["ts"]) - t.entry_time).total_seconds()) <= 3600
    ]
    return {"events": items, "high_impact_near_entry": len(high_near) > 0, "note": CAUSATION_NOTE}


def _simulate(
    bars: list[Bar], start: int, direction: str, entry: float, stop: float, target: float | None, tick: float
) -> tuple[float, str, datetime] | None:
    """Conservative exit simulation from bar index ``start`` (same rules as the backtester)."""
    s = 1 if direction == "long" else -1
    for j in range(start, len(bars)):
        b = bars[j]
        if j > start and b.ts - bars[j - 1].ts > timedelta(minutes=2):
            return bars[j - 1].close - s * tick, "data gap / session end", bars[j - 1].ts
        if b.ts.astimezone(ET).time() >= FLAT_BY:
            return b.open - s * tick, "flat by 15:55 ET", b.ts
        if (b.low <= stop) if s == 1 else (b.high >= stop):
            gap = b.open < stop if s == 1 else b.open > stop
            return (b.open if gap else stop) - s * tick, "stop", b.ts
        if target is not None and ((b.high >= target + tick) if s == 1 else (b.low <= target - tick)):
            return target, "target", b.ts
    if start < len(bars):
        return bars[-1].close - s * tick, "end of stored data", bars[-1].ts
    return None


def scenarios(d: TradeData, commission_per_side: float) -> list[dict[str, Any]]:
    t = d.trade
    tick, pv = _tv(t.symbol)
    s = 1 if t.direction == "long" else -1
    stop, target = _stop_target(d)
    out: list[dict[str, Any]] = []

    def pnl(entry: float, exit_: float, qty: int) -> float:
        return round((exit_ - entry) * s * pv * qty - 2 * commission_per_side * qty, 2)

    half = max(t.quantity // 2, 1)
    if t.quantity > 1:
        out.append(
            {
                "name": "Smaller position",
                "detail": f"{half} instead of {t.quantity} contracts, same prices",
                "pnl": pnl(t.entry_price, t.exit_price, half),
                "available_at_entry": True,
            }
        )
    times = [b.ts for b in d.bars]
    i = bisect.bisect_left(times, t.entry_time.replace(second=0, microsecond=0))
    if stop is not None and i < len(d.bars):
        sim = _simulate(d.bars, i, t.direction, t.entry_price, stop, target, tick)
        if sim:
            out.append(
                {
                    "name": "Followed the original plan",
                    "detail": f"Held to the planned stop {stop}"
                    + (f" / target {target}" if target else "")
                    + f"; exit by {sim[1]}",
                    "pnl": pnl(t.entry_price, sim[0], t.quantity),
                    "exit_time": sim[2].isoformat(),
                    "available_at_entry": True,
                }
            )
        # Waited for confirmation: first 5-minute close beyond entry in the trade direction.
        risk = abs(t.entry_price - stop)
        j = i
        while j < len(d.bars) and d.bars[j].ts < t.exit_time:
            b = d.bars[j]
            if (b.ts.minute + 1) % 5 == 0 and (b.close - t.entry_price) * s >= 0.25 * risk:
                break
            j += 1
        if j < len(d.bars) - 1 and d.bars[j].ts < t.exit_time:
            entry2 = d.bars[j + 1].open + s * tick
            stop2 = entry2 - s * risk
            target2 = entry2 + s * abs(target - t.entry_price) if target is not None else None
            sim2 = _simulate(d.bars, j + 1, t.direction, entry2, stop2, target2, tick)
            if sim2:
                out.append(
                    {
                        "name": "Waited for confirmation",
                        "detail": f"Entered at {entry2:.2f} after a 5-minute close 0.25R in favour; same risk distance",
                        "pnl": pnl(entry2, sim2[0], t.quantity),
                        "available_at_entry": True,
                    }
                )
        else:
            out.append(
                {
                    "name": "Waited for confirmation",
                    "detail": "No confirming 5-minute close occurred before the "
                    "actual exit, so this alternative would not have entered.",
                    "pnl": 0.0,
                    "available_at_entry": True,
                }
            )
    if any(
        e.importance == "high"
        and t.symbol in (e.affected or [])
        and timedelta(0) <= e.ts - t.entry_time <= timedelta(minutes=30)
        for e in d.events
    ):
        out.append(
            {
                "name": "Avoided the trade before a high-impact event",
                "detail": "No position taken",
                "pnl": 0.0,
                "available_at_entry": True,
            }
        )
    actual = round(t.net_pnl, 2)
    for o in out:
        o["difference_vs_actual"] = round(o["pnl"] - actual, 2)
        o["hypothetical"] = True
    return out


def coach(
    d: TradeData,
    ctx: dict[str, Any],
    news: dict[str, Any],
    mfe: float | None,
    mae: float | None,
    daily_loss_limit: float,
    max_contracts: float,
) -> dict[str, Any]:
    """Evidence-based findings. Each finding cites what was observed; nothing about emotions."""
    t = d.trade
    tick, pv = _tv(t.symbol)
    s = 1 if t.direction == "long" else -1
    stop, target = _stop_target(d)
    findings: list[dict[str, Any]] = []
    good: list[str] = []

    def add(code: str, title: str, evidence: str, severity: str = "medium") -> None:
        findings.append(
            {
                "code": code,
                "title": title,
                "evidence": evidence,
                "severity": severity,
                "basis": "observed in trade records and stored market data",
            }
        )

    risk_per = abs(t.entry_price - stop) * pv if stop is not None else None
    if stop is None:
        add(
            "no_stop",
            "No stop-loss recorded",
            "No planned stop was recorded for this trade, so risk at entry cannot be verified.",
            "high",
        )
    else:
        good.append(f"A stop was defined at {stop} ({abs(t.entry_price - stop) / tick:.0f} ticks).")
        a = ctx.get("atr_5m") if ctx.get("available") else None
        if a and abs(t.entry_price - stop) < 0.5 * a:
            add(
                "tight_stop",
                "Stop placed inside normal noise",
                f"Stop distance {abs(t.entry_price - stop):.2f} was below half of the 5-minute ATR ({a:.2f}) at entry.",
            )
        total_risk = risk_per * t.quantity
        if total_risk > 0.5 * daily_loss_limit:
            add(
                "oversized",
                "Position risk large relative to the daily allowance",
                f"Risk to stop ${total_risk:,.2f} was more than half of the ${daily_loss_limit:,.0f} daily loss limit.",
                "high",
            )
        else:
            good.append(f"Risk to stop (${total_risk:,.2f}) was within half of the daily loss allowance.")
        if target is not None:
            rr = abs(target - t.entry_price) / abs(t.entry_price - stop) if stop != t.entry_price else 0
            if rr < 1.0:
                add("poor_rr", "Unfavourable planned reward:risk", f"Planned reward:risk was {rr:.2f} (below 1.0).")
            else:
                good.append(f"Planned reward:risk was {rr:.2f}.")
        if (t.exit_price - stop) * s < -tick:
            add(
                "beyond_stop",
                "Exit beyond the planned stop",
                f"Exit {t.exit_price} was beyond the planned stop {stop}; the plan's risk was exceeded.",
                "high",
            )
    if t.quantity * float(INSTRUMENTS[t.symbol].standard_equivalent) > max_contracts:
        add("over_limit", "Contract limit exceeded", f"{t.quantity} contracts exceeded the configured limit.", "high")
    if ctx.get("available"):
        tr = ctx["trend"]
        against = [tf for tf in ("5m", "15m") if tr.get(tf) == ("down" if s == 1 else "up")]
        if len(against) == 2:
            add(
                "against_trend",
                "Entry against the prevailing trend",
                f"Both 5m and 15m trend definitions were {'down' if s == 1 else 'up'} at entry for a {t.direction}.",
            )
        elif tr.get("5m") == ("up" if s == 1 else "down"):
            good.append("Entry direction agreed with the 5-minute trend.")
        if ctx.get("volatility") == "elevated":
            add(
                "high_volatility",
                "Entry during elevated volatility",
                f"5-minute ATR was {ctx['volatility_ratio']}x its recent median at entry.",
                "low",
            )
    for e in news["events"]:
        if e["importance"] == "high" and e["phase"] == "while_open":
            add(
                "before_news",
                "Position open through a high-impact event",
                f"{e['title']} at {datetime.fromisoformat(e['ts']).astimezone(ET):%H:%M} ET occurred while the "
                "trade was open" + (" and was scheduled before entry." if e["known_before_entry"] else "."),
            )
    if mfe is not None and mae is not None and risk_per:
        r = risk_per * t.quantity
        if mae >= 0.5 * r and t.net_pnl > 0:
            add(
                "early_entry",
                "Entry may have been early",
                f"Price moved {mae / r:.2f}R against the position before the trade worked.",
                "low",
            )
        if mfe >= 1.0 * r and t.net_pnl <= 0:
            add(
                "gave_back",
                "Open profit was given back",
                f"The trade reached {mfe / r:.2f}R in favour before closing at a loss.",
            )
    if d.signal is not None:
        chase = abs(t.entry_price - d.signal.entry) / tick
        if chase > 8:
            add(
                "late_entry",
                "Entry far from the signal price",
                f"Entry was {chase:.0f} ticks from the strategy signal entry ({d.signal.entry}).",
                "low",
            )
        else:
            good.append(f"Entry was close to the {d.signal.strategy_id} signal price.")
    if d.plan is not None and d.plan.entry is not None and abs(t.entry_price - d.plan.entry) / tick > 8:
        add(
            "plan_entry",
            "Entry differed from the trade plan",
            f"Plan entry {d.plan.entry}, actual {t.entry_price}.",
            "low",
        )
    if t.followed_plan is False:
        add("plan_not_followed", "Marked as not following the plan", "You recorded that the plan was not followed.")
    n_day = len(d.day_trades)
    if n_day > 6:
        add("overtrading", "High trade count for the day", f"{n_day} trades on {t.trading_date}.", "low")
    prev = [x for x in d.day_trades if x.exit_time <= t.entry_time and x.id != t.id]
    if (
        prev
        and prev[-1].net_pnl < 0
        and (t.entry_time - prev[-1].exit_time) <= timedelta(minutes=10)
        and t.quantity >= prev[-1].quantity
    ):
        add(
            "rapid_reentry",
            "Rapid re-entry after a loss with equal or larger size",
            f"Entered {int((t.entry_time - prev[-1].exit_time).total_seconds() // 60)} min after a losing trade, "
            f"with {t.quantity} vs {prev[-1].quantity} contracts. This pattern is sometimes associated with "
            "revenge trading; the records alone cannot show intent.",
            "medium",
        )
    return {"findings": findings, "did_well": good}


def build_review(
    d: TradeData, settings: Settings, daily_loss_limit: float, max_contracts: float
) -> tuple[dict[str, Any], dict[str, Any], float | None, float | None]:
    t = d.trade
    _, pv = _tv(t.symbol)
    s = 1 if t.direction == "long" else -1
    stop, target = _stop_target(d)
    mfe, mae, held = excursions(d)
    ctx = market_context(d)
    news = news_attribution(d)
    commission = (
        settings.cost_commission_per_side_micro
        if INSTRUMENTS[t.symbol].is_micro
        else settings.cost_commission_per_side_standard
    )
    alts = scenarios(d, commission)
    c = coach(d, ctx, news, mfe, mae, daily_loss_limit, max_contracts)
    duration = (t.exit_time - t.entry_time).total_seconds()
    r_mult = (
        ((t.exit_price - t.entry_price) * s / abs(t.entry_price - stop)) if stop not in (None, t.entry_price) else None
    )
    signal = None
    if d.plan and d.plan.signal_snapshot:
        signal = d.plan.signal_snapshot
    elif d.signal is not None:
        signal = {
            "label": "BUY" if d.signal.direction == "long" else "SHORT",
            "strategy_id": d.signal.strategy_id,
            "entry": d.signal.entry,
            "stop": d.signal.stop,
            "targets": d.signal.targets,
            "detected_at": d.signal.created_at.isoformat(),
            "quality": d.signal.quality,
        }

    context = {
        "provider": d.provider,
        "data_quality": d.signal.quality if d.signal else None,
        "bars_during_trade": len(held),
        "market": ctx,
        "news": news,
        "signal_at_entry": signal,
        "plan": None
        if d.plan is None
        else {
            "id": d.plan.id,
            "thesis": d.plan.thesis,
            "entry": d.plan.entry,
            "stop": d.plan.stop,
            "target": d.plan.target,
            "quantity": d.plan.quantity,
            "strategy_id": d.plan.strategy_id,
            "execution": d.plan.execution,
        },
        "duration_seconds": duration,
        "r_multiple": round(r_mult, 2) if r_mult is not None else None,
    }

    fmt_t = lambda x: x.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")  # noqa: E731
    sim = (
        "simulated (paper)"
        if t.source == "paper"
        else "imported broker record"
        if t.source == "csv"
        else "manual entry"
    )
    mins = int(duration // 60)
    events_txt = "; ".join(f"{e['title']} ({e['phase'].replace('_', ' ')})" for e in news["events"]) or "none recorded"
    findings_txt = [f["title"] for f in c["findings"]]
    adherence = (
        "Exit beyond the planned stop."
        if any(f["code"] == "beyond_stop" for f in c["findings"])
        else "Exit within the planned risk."
        if stop is not None
        else "No plan recorded to compare against."
    )
    journal = {
        "trade_summary": f"{t.direction.title()} {t.quantity} {t.symbol} ({sim}) from {t.entry_price} at "
        f"{fmt_t(t.entry_time)} to {t.exit_price} at {fmt_t(t.exit_time)}, held {mins} min.",
        "entry_analysis": (
            d.plan.thesis
            if d.plan and d.plan.thesis
            else f"Strategy signal {signal['strategy_id']} ({signal['label']}) preceded the entry."
            if signal
            else "No written thesis or matching strategy signal was recorded."
        ),
        "market_conditions": (
            f"Regime {ctx['regime'].replace('_', ' ')}; trend 5m {ctx['trend']['5m']}, 15m "
            f"{ctx['trend']['15m']}, 1h {ctx['trend']['1h']}; "
            + (f"price {ctx['vs_vwap']} VWAP; " if ctx.get("vs_vwap") else "VWAP unavailable (no session volume yet); ")
            + f"volatility {ctx['volatility']}."
            if ctx.get("available")
            else ctx.get("reason")
        ),
        "news_context": f"Events: {events_txt}. {CAUSATION_NOTE}",
        "risk_analysis": (
            f"Risk to stop ${abs(t.entry_price - stop) * pv * t.quantity:,.2f} for {t.quantity} contract(s)"
            + (f"; planned target {target}." if target else "; no target recorded.")
            if stop is not None
            else "No stop recorded; risk could not be assessed."
        ),
        "execution_review": adherence + (f" Slippage ${t.slippage:,.2f}." if t.slippage else ""),
        "outcome": f"Net {'+' if t.net_pnl >= 0 else ''}${t.net_pnl:,.2f} after ${t.fees:,.2f} fees"
        + (f" ({r_mult:+.2f}R)." if r_mult is not None else ".")
        + (f" MFE ${mfe:,.2f}, MAE ${mae:,.2f}." if mfe is not None else ""),
        "improvement_opportunities": "; ".join(findings_txt)
        if findings_txt
        else "No rule-based issues were found in the recorded data.",
        "lesson": _lesson(c["findings"], t.net_pnl),
    }
    coaching = {
        "did_well": c["did_well"] or ["No specific strengths could be identified from the recorded data."],
        "could_improve": [f["title"] for f in c["findings"]],
        "supported_mistakes": c["findings"],
        "information_at_entry": {
            "market": ctx if ctx.get("available") else None,
            "scheduled_events_known": [e["title"] for e in news["events"] if e["known_before_entry"]],
            "signal": signal,
            "plan_thesis": d.plan.thesis if d.plan else None,
        },
        "after_entry": (
            f"Best open profit ${mfe:,.2f}, worst open loss ${mae:,.2f} over {len(held)} one-minute bars."
            if mfe is not None
            else "No stored bars cover the holding period."
        ),
        "alternatives": [a["name"] for a in alts],
        "next_objective": _objective(c["findings"]),
        "outcome_note": "Outcome alone does not judge the decision: a loss can follow a sound plan and a profit "
        "can follow a poor one.",
    }
    review = {
        "version": REVIEW_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "journal": journal,
        "coaching": coaching,
        "scenarios": alts,
        "timeline": timeline(t, news, signal, d.plan),
    }
    return context, review, mfe, mae


def _lesson(findings: list[dict[str, Any]], pnl: float) -> str:
    if not findings:
        return (
            "The recorded process met the checked rules. Keep recording plans so results can be judged "
            "on process as well as outcome."
        )
    top = sorted(findings, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["severity"]])[0]
    return f"Main process point: {top['title'].lower()}. {top['evidence']}"


def _objective(findings: list[dict[str, Any]]) -> str:
    codes = {f["code"] for f in findings}
    for code, text in (
        ("no_stop", "Record a stop-loss in the plan before every entry."),
        ("beyond_stop", "Exit at or before the planned stop on the next 5 trades."),
        ("oversized", "Size each trade so risk to stop is at most 25% of the daily allowance."),
        ("before_news", "Check the calendar and stand aside 15 minutes before high-impact events."),
        ("against_trend", "Take the next 5 trades only in the direction of the 15-minute trend."),
        ("rapid_reentry", "Wait at least 15 minutes after a losing trade before the next entry."),
        ("poor_rr", "Only take setups with planned reward:risk of at least 1.5."),
        ("tight_stop", "Place stops beyond at least half of the 5-minute ATR."),
    ):
        if code in codes:
            return text
    return "Keep writing a plan (entry, stop, target, thesis) before each trade so the next review can compare."


def timeline(
    t: JournalTrade, news: dict[str, Any], signal: dict[str, Any] | None, plan: TradePlan | None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for e in news["events"]:
        items.append({"ts": e["ts"], "kind": "news", "text": f"{e['title']} ({e['importance']})"})
        if e["reaction"]:
            items.append({"ts": e["ts"], "kind": "reaction", "text": e["description"]})
    if plan is not None:
        items.append({"ts": plan.created_at.isoformat(), "kind": "plan", "text": "Trade plan written"})
    if signal is not None and signal.get("detected_at"):
        items.append(
            {
                "ts": signal["detected_at"],
                "kind": "signal",
                "text": f"{signal['label']} signal ({signal.get('strategy_id')})",
            }
        )
    items.append(
        {"ts": t.entry_time.isoformat(), "kind": "entry", "text": f"Entry {t.direction} {t.quantity} @ {t.entry_price}"}
    )
    items.append(
        {"ts": t.exit_time.isoformat(), "kind": "exit", "text": f"Exit @ {t.exit_price} (net {t.net_pnl:+.2f})"}
    )
    return sorted(items, key=lambda x: x["ts"])


async def review_trade(session: AsyncSession, trade: JournalTrade, settings: Settings) -> JournalTrade:
    from app.risk.profile import load_rules, to_ruleset

    rules = to_ruleset(await load_rules(session))
    d = await load_trade_data(session, trade, settings)
    context, review, mfe, mae = build_review(d, settings, float(rules.daily_loss_limit), float(rules.max_contracts))
    trade.context, trade.review, trade.mfe, trade.mae = context, review, mfe, mae
    if trade.planned_stop is None and d.plan is not None:
        trade.planned_stop, trade.planned_target = d.plan.stop, d.plan.target
    if trade.strategy_id is None and d.plan is not None and d.plan.strategy_id:
        trade.strategy_id = d.plan.strategy_id
    trade.reviewed_at = datetime.now(UTC)
    return trade


async def replay_data(session: AsyncSession, trade: JournalTrade, settings: Settings) -> dict[str, Any]:
    """Bars, markers and events for replay. The client reveals them up to the replay cursor only."""
    provider = settings.market_data_provider
    start = trade.entry_time - timedelta(minutes=90)
    end = trade.exit_time + timedelta(minutes=30)
    bars = await load_bars(session, provider, trade.symbol, start, end) if provider != "none" else []
    if len(bars) > 900:
        bars = bars[-900:]
    events = (
        await session.scalars(
            select(EconomicEvent)
            .where(EconomicEvent.ts >= start - timedelta(hours=12), EconomicEvent.ts <= end)
            .order_by(EconomicEvent.ts)
        )
    ).all()
    ctx = trade.context or {}
    return {
        "trade_id": trade.id,
        "symbol": trade.symbol,
        "provider": provider,
        "bars": [
            {"t": int(b.ts.timestamp()), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
            for b in bars
        ],
        "entry": {"t": int(trade.entry_time.timestamp()), "price": trade.entry_price, "direction": trade.direction},
        "exit": {"t": int(trade.exit_time.timestamp()), "price": trade.exit_price},
        "stop": trade.planned_stop,
        "target": trade.planned_target,
        "net_pnl": trade.net_pnl,
        "signal_at_entry": ctx.get("signal_at_entry"),
        "market_at_entry": ctx.get("market"),
        # Each event carries when it became known; the client hides it before that time.
        "events": [
            {
                "t": int(e.ts.timestamp()),
                "known_from": int(min(e.ts, e.created_at).timestamp()) if e.created_at else int(e.ts.timestamp()),
                "title": e.title,
                "importance": e.importance,
                "affected": e.affected,
            }
            for e in events
            if trade.symbol in (e.affected or [])
        ],
        "hindsight": (trade.review or {}).get("coaching"),
        "scenarios": (trade.review or {}).get("scenarios"),
    }
