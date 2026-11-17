"""Live Trade Entry Confirmation: a deterministic, explainable decision over 15 conditions.

States: LONG READY, SHORT READY, WAIT, NO TRADE, DATA UNAVAILABLE.

* Every condition is PASS / FAIL / WAIT / UNKNOWN with a reason and the evidence used. Missing data is
  UNKNOWN, never PASS.
* Each strategy declares which conditions are MANDATORY (all must PASS for READY) and which are
  OPTIONAL (reported, never block). A mandatory FAIL that marks the setup unsafe gives NO TRADE;
  a mandatory WAIT/UNKNOWN gives WAIT with what is missing and what would confirm it.
* Levels (entry zone, stop, targets) come only from a strategy signal built on market structure;
  they are rounded to the instrument tick, costs and slippage are included, and nothing is invented
  when there is no signal.
* The Risk Guardian is never bypassed: account state missing or stale -> UNKNOWN; a rule violation or
  zero allowed size -> NO TRADE; an unverified rule profile is never described as compliant.
* Analysis only. Nothing here submits, modifies or cancels an order.

``decide`` is pure (all inputs passed in), so it is unit-tested with fixed inputs; ``confirmation``
gathers those inputs from stored data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate, closed_only
from app.research.indicators import atr, session_vwap
from app.services.checklist import STYLE_WINDOWS, ChecklistConfig, reward_risk
from app.services.session_intel import market_status, session_bounds

PASS, FAIL, WAIT, UNKNOWN = "PASS", "FAIL", "WAIT", "UNKNOWN"
LONG_READY, SHORT_READY, NO_TRADE, DATA_UNAVAILABLE = "LONG READY", "SHORT READY", "NO TRADE", "DATA UNAVAILABLE"
WAIT_STATE = "WAIT"

CONDITIONS: tuple[tuple[str, str], ...] = (
    ("htf_alignment", "Higher-timeframe alignment"),
    ("structure", "Market structure"),
    ("trigger", "Entry trigger"),
    ("ob_fvg", "Order block / fair value gap"),
    ("entry_zone", "Entry-zone proximity"),
    ("vwap", "VWAP position and crosses"),
    ("rvol", "Relative volume"),
    ("volatility", "Volatility"),
    ("session", "New York session quality"),
    ("news", "News"),
    ("reward_risk", "Risk-to-reward"),
    ("risk", "Apex risk eligibility"),
    ("data", "Data freshness"),
    ("invalidation", "Invalidation"),
    ("history", "Historical performance"),
)
NAMES = dict(CONDITIONS)

BASE_MANDATORY = frozenset(
    {
        "htf_alignment",
        "structure",
        "trigger",
        "entry_zone",
        "session",
        "news",
        "reward_risk",
        "risk",
        "data",
        "invalidation",
    }
)
#: Per-strategy changes to the mandatory set. Counter-trend strategies do not require trend alignment.
STRATEGY_RULES: dict[str, dict[str, frozenset[str]]] = {
    "orb": {"add": frozenset({"rvol"})},
    "vwap_trend": {"add": frozenset({"vwap"})},
    "sweep_reversal": {"drop": frozenset({"htf_alignment", "structure"})},
    "breakout_retest": {"add": frozenset({"rvol"})},
    "momentum_pullback": {"add": frozenset({"vwap"})},
    "vol_expansion": {"add": frozenset({"rvol", "volatility"})},
    "mean_reversion": {"drop": frozenset({"htf_alignment", "structure"})},
    "mtf_trend": {},
    "smc_sweep_mss": {"drop": frozenset({"htf_alignment"})},
    "smc_ob_retest": {"add": frozenset({"ob_fvg"})},
    "smc_fvg_retest": {"add": frozenset({"ob_fvg"})},
}
#: A FAIL on these means the trade is unsafe now (NO TRADE); a FAIL elsewhere means "not yet" (WAIT).
HARD_FAIL = frozenset({"htf_alignment", "news", "reward_risk", "risk", "data", "invalidation", "structure"})
ZONE_TYPES = {
    "long": ("bullish_order_block", "demand_zone", "bullish_fvg"),
    "short": ("bearish_order_block", "supply_zone", "bearish_fvg"),
}
EXPIRY_BARS = 3  # a signal is actionable for 3 bars of its timeframe (5m) after the trigger bar closed
MIN_RR = 1.5


def mandatory_for(strategy_id: str | None) -> frozenset[str]:
    r = STRATEGY_RULES.get(strategy_id or "", {})
    return (BASE_MANDATORY | r.get("add", frozenset())) - r.get("drop", frozenset())


def _c(key: str, status: str, reason: str, evidence: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    return {"key": key, "name": NAMES[key], "status": status, "reason": reason, "evidence": evidence or {}, **kw}


def _rnd(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 6)


def next_eligible_window(symbol: str, now: datetime, style: str = "intraday") -> dict[str, Any] | None:
    """Start of the next preferred New York window on a trading day with an exchange session."""
    windows = STYLE_WINDOWS.get(style, STYLE_WINDOWS["intraday"])
    local = now.astimezone(ET)
    for add in range(0, 8):
        d = local.date() + timedelta(days=add)
        if session_bounds(symbol, d) is None:
            continue
        for a, b in windows:
            start = datetime.combine(d, a, ET).astimezone(UTC)
            end = datetime.combine(d, b, ET).astimezone(UTC)
            if start <= now.astimezone(UTC) < end:
                return {
                    "start": start.isoformat(),
                    "start_et": f"{a:%H:%M}",
                    "end_et": f"{b:%H:%M}",
                    "now": True,
                    "date": d.isoformat(),
                }
            if start > now.astimezone(UTC) and market_status(symbol, start + timedelta(minutes=1))["open"]:
                return {
                    "start": start.isoformat(),
                    "start_et": f"{a:%H:%M}",
                    "end_et": f"{b:%H:%M}",
                    "date": d.isoformat(),
                    "in_minutes": int((start - now.astimezone(UTC)).total_seconds() // 60),
                    "now": False,
                }
    return None


def _vwap_state(bars_1m: Sequence[Bar], now: datetime) -> dict[str, Any]:
    b5 = closed_only(aggregate(list(bars_1m), Timeframe.M5), Timeframe.M5, now)
    if not b5:
        return {"vwap": None}
    v, _sd = session_vwap(b5)
    last = v[-1] if v else None
    return {"vwap": round(last, 4) if last is not None else None, "close": b5[-1].close, "at": b5[-1].ts.isoformat()}


def decide(
    symbol: str,
    now: datetime,
    bars_1m: Sequence[Bar],
    readiness: dict[str, Any],
    signal: dict[str, Any] | None,
    risk_check: dict[str, Any] | None,
    zones: Sequence[dict[str, Any]] = (),
    liquidity: Sequence[dict[str, Any]] = (),
    history: dict[str, Any] | None = None,
    volatility: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
    cfg: ChecklistConfig = ChecklistConfig(),
    style: str = "intraday",
) -> dict[str, Any]:
    inst = INSTRUMENTS[symbol]
    tick = float(inst.tick_size)
    pv = float(inst.point_value)
    bars = [b for b in bars_1m if b.ts + timedelta(minutes=1) <= now]
    checklist = readiness.get("checklist") or {}
    items = {i["key"]: i for i in checklist.get("items", [])}
    data = readiness.get("data") or {}
    news = readiness.get("news") or {}
    market = checklist.get("market") or market_status(symbol, now)
    price = bars[-1].close if bars else None
    direction = signal.get("direction") if signal else None
    want = "bullish" if direction == "long" else "bearish" if direction == "short" else None
    lean = checklist.get("direction")
    conds: list[dict[str, Any]] = []
    b5 = closed_only(aggregate(bars, Timeframe.M5), Timeframe.M5, now)
    atr5 = (atr(b5, 14)[-1] if len(b5) > 15 else None) or None

    # 13. Data freshness (first: everything else depends on it)
    ds = data.get("status", "UNAVAILABLE")
    if ds == "UNAVAILABLE" or not bars:
        conds.append(_c("data", UNKNOWN, data.get("reason") or "No market data stored for this instrument.", data))
    elif ds == "STALE":
        conds.append(_c("data", FAIL, data.get("reason") or "Market data is stale.", data))
    elif ds == "HISTORICAL" and market.get("open"):
        conds.append(
            _c(
                "data",
                FAIL,
                "Only HISTORICAL data is available while the market is open; the live stream "
                "is not delivering bars, so an entry cannot be confirmed.",
                data,
            )
        )
    elif ds in ("LIVE", "SIMULATED"):
        conds.append(
            _c(
                "data",
                PASS,
                f"{ds} data, newest completed bar {data.get('age_seconds')} s old"
                + (" (SIMULATED: not real market data)." if ds == "SIMULATED" else "."),
                data,
            )
        )
    else:
        conds.append(_c("data", WAIT, f"Data class {ds}; the market is closed.", data))

    # 1. HTF alignment (15m and 1h structure)
    trends = ((items.get("alignment") or {}).get("evidence") or {}).get("trends") or {}
    htf = {k: trends.get(k, "unknown") for k in ("15m", "1h")}
    ref = want or lean
    if not trends or all(v == "unknown" for v in htf.values()):
        conds.append(
            _c("htf_alignment", UNKNOWN, "Not enough completed 15m/1h candles for structure.", {"trends": trends})
        )
    elif {"bullish", "bearish"} <= set(htf.values()):
        conds.append(
            _c(
                "htf_alignment",
                FAIL,
                f"15m {htf['15m']} vs 1h {htf['1h']}: higher timeframes conflict.",
                {"trends": trends},
            )
        )
    elif ref and any(v not in (ref, "neutral", "unknown") for v in htf.values()):
        conds.append(
            _c(
                "htf_alignment",
                FAIL,
                f"Setup is {direction or ref} but 15m is {htf['15m']} and 1h is {htf['1h']}.",
                {"trends": trends},
            )
        )
    elif ref and all(v == ref for v in htf.values()):
        conds.append(_c("htf_alignment", PASS, f"15m and 1h structure both {ref}.", {"trends": trends}))
    else:
        conds.append(
            _c(
                "htf_alignment",
                WAIT,
                f"Higher timeframes not yet aligned (15m {htf['15m']}, 1h {htf['1h']}).",
                {"trends": trends},
                confirm="15m and 1h structure in the setup's direction",
            )
        )

    # 2. Market structure (5m)
    t5 = trends.get("5m", "unknown")
    if t5 == "unknown":
        conds.append(_c("structure", UNKNOWN, "Not enough completed 5m candles.", {"5m": t5}))
    elif ref is None:
        conds.append(_c("structure", WAIT, f"5m structure {t5}; no setup direction yet.", {"5m": t5}))
    elif t5 == ref and regime and "choppy" in (regime.get("labels") or []):
        conds.append(
            _c(
                "structure",
                WAIT,
                f"5m structure {t5}, but the regime is choppy (efficiency ratio "
                f"{(regime.get('evidence') or {}).get('efficiency_ratio_2h')}, "
                f"{(regime.get('evidence') or {}).get('vwap_crosses_today')} VWAP crosses): breaks are unreliable.",
                {"5m": t5, "regime": regime.get("labels")},
                confirm="a directional regime (efficiency ratio >= 0.35, fewer VWAP crosses)",
            )
        )
    elif t5 == ref:
        conds.append(_c("structure", PASS, f"5m structure {t5} (BOS/CHoCH on completed candles).", {"5m": t5}))
    elif t5 == "neutral":
        conds.append(
            _c(
                "structure",
                WAIT,
                "5m structure neutral; no break of structure yet.",
                {"5m": t5},
                confirm=f"a 5m break of structure to the {'upside' if ref == 'bullish' else 'downside'}",
            )
        )
    else:
        conds.append(_c("structure", FAIL, f"5m structure is {t5}, against the {direction or ref} setup.", {"5m": t5}))

    # 3. Trigger + expiry
    expiry = None
    if signal:
        sig_ts = datetime.fromisoformat(signal["detected_at"])
        trig_close = sig_ts + timedelta(minutes=5)
        expiry = trig_close + timedelta(minutes=5 * EXPIRY_BARS)
        if now <= expiry:
            conds.append(
                _c(
                    "trigger",
                    PASS,
                    f"{signal['strategy_id']} {direction} trigger on the 5m bar closed "
                    f"{trig_close.astimezone(ET):%H:%M} ET.",
                    {"signal": signal},
                )
            )
        else:
            conds.append(
                _c(
                    "trigger",
                    WAIT,
                    f"The trigger expired at {expiry.astimezone(ET):%H:%M} ET ({EXPIRY_BARS} bars after it fired).",
                    {"signal": signal},
                    confirm="a fresh strategy trigger on a completed 5m bar",
                )
            )
    elif ds in ("UNAVAILABLE", "STALE"):
        conds.append(_c("trigger", UNKNOWN, "Market data is unavailable or stale."))
    else:
        conds.append(
            _c(
                "trigger",
                WAIT,
                "No strategy's entry conditions are met on the latest completed bar.",
                confirm="a strategy signal on a completed 5m bar",
            )
        )

    # 4. Order block / FVG
    if direction:
        lo, hi = float(signal["entry_low"]), float(signal["entry_high"])
        hit = [
            z
            for z in zones
            if z.get("type") in ZONE_TYPES[direction]
            and z.get("status") not in ("invalidated", "filled")
            and z["bottom"] <= hi
            and z["top"] >= lo
        ]
        conds.append(
            _c(
                "ob_fvg",
                PASS if hit else WAIT,
                f"Entry zone overlaps {hit[0]['type'].replace('_', ' ')} {hit[0]['bottom']}-{hit[0]['top']}."
                if hit
                else "No unmitigated order block / FVG overlaps the entry zone.",
                {"zones": hit[:3]},
                confirm="price retesting an unmitigated OB/FVG in the setup's direction",
            )
        )
    else:
        conds.append(_c("ob_fvg", WAIT, "No setup direction to match a zone against."))

    # 5. Entry-zone proximity and 14. Invalidation
    if signal and price is not None:
        lo, hi = float(signal["entry_low"]), float(signal["entry_high"])
        s = 1 if direction == "long" else -1
        stop = float(signal["stop"])
        since = datetime.fromisoformat(signal["detected_at"]) + timedelta(minutes=5)
        after = [b for b in bars if b.ts >= since]
        breached = any((b.low <= stop) if s == 1 else (b.high >= stop) for b in after)
        tol = 0.5 * atr5 if atr5 else 4 * tick
        dist = 0.0 if lo <= price <= hi else (lo - price if price < lo else price - hi)
        chased = (price - hi) > tol if s == 1 else (lo - price) > tol
        ev = {"price": price, "zone": [lo, hi], "distance_points": round(dist, 4), "tolerance_points": round(tol, 4)}
        if breached:
            conds.append(_c("entry_zone", FAIL, "Price traded through the stop after the trigger.", ev))
        elif dist == 0:
            conds.append(_c("entry_zone", PASS, f"Price {price} is inside the entry zone {lo}-{hi}.", ev))
        elif dist <= tol:
            conds.append(
                _c("entry_zone", PASS, f"Price {price} is {dist:.2f} pts from the zone (within 0.5 x ATR).", ev)
            )
        elif chased:
            conds.append(
                _c(
                    "entry_zone",
                    WAIT,
                    f"Price has moved {dist:.2f} pts beyond the zone in the trade direction; "
                    "do not chase. Wait for a pullback into the zone.",
                    ev,
                    confirm=f"a pullback into {lo}-{hi}",
                )
            )
        else:
            conds.append(
                _c(
                    "entry_zone",
                    WAIT,
                    f"Price {price} is {dist:.2f} pts from the zone.",
                    ev,
                    confirm=f"price returning to {lo}-{hi}",
                )
            )
        conds.append(
            _c(
                "invalidation",
                FAIL if breached else PASS,
                f"Stop {stop} was traded through after the trigger: the setup is invalid."
                if breached
                else f"Not invalidated: price has not traded through {stop} since the trigger.",
                {"invalidation_price": stop},
            )
        )
    else:
        conds.append(
            _c(
                "entry_zone",
                UNKNOWN if price is None else WAIT,
                "No setup: there is no entry zone to compare with." if price is not None else "No price.",
            )
        )
        conds.append(_c("invalidation", UNKNOWN if price is None else WAIT, "No setup to invalidate."))

    # 6. VWAP
    vw = _vwap_state(bars, now)
    crosses_item = items.get("vwap_crosses") or {}
    ncross = (crosses_item.get("evidence") or {}).get("count")
    if vw.get("vwap") is None:
        conds.append(_c("vwap", UNKNOWN, "Session VWAP starts at 09:30 ET; no regular-session bars yet.", vw))
    elif not direction:
        conds.append(_c("vwap", WAIT, f"Price {vw['close']} vs VWAP {vw['vwap']}; no setup direction.", vw))
    else:
        side_ok = (vw["close"] > vw["vwap"]) if direction == "long" else (vw["close"] < vw["vwap"])
        too_many = ncross is not None and ncross >= cfg.chop_max_crosses
        st = FAIL if too_many else PASS if side_ok else WAIT
        conds.append(
            _c(
                "vwap",
                st,
                f"5m close {vw['close']} is {'above' if vw['close'] > vw['vwap'] else 'below'} "
                f"VWAP {vw['vwap']}; {ncross if ncross is not None else '?'} confirmed crosses today.",
                {**vw, "crosses_today": ncross},
                confirm=f"a 5m close {'above' if direction == 'long' else 'below'} VWAP",
            )
        )

    # 7. Relative volume
    rv = items.get("volume") or {}
    rve = rv.get("evidence") or {}
    if rv.get("status") in (None, UNKNOWN):
        conds.append(_c("rvol", UNKNOWN, rv.get("reason") or "Relative volume not available.", rve))
    else:
        conds.append(
            _c(
                "rvol",
                rv["status"] if rv["status"] == PASS else WAIT,
                rv["reason"]
                + (
                    f" ({rve.get('data_quality')} baseline, {rve.get('reference_sessions')} sessions)."
                    if rve.get("data_quality")
                    else ""
                ),
                rve,
                confirm=f"relative volume >= {cfg.min_rvol}",
            )
        )

    # 8. Volatility
    vol = volatility or {}
    if not vol.get("available") or vol.get("level") == "unknown":
        conds.append(
            _c(
                "volatility",
                UNKNOWN,
                vol.get("note") or vol.get("reason") or "Volatility percentile needs at least 5 prior sessions.",
                vol,
            )
        )
    elif vol["level"] == "unusually high":
        conds.append(
            _c(
                "volatility",
                FAIL,
                f"ATR percentile {vol['atr_percentile_same_time']} for this time of day: "
                "unusually high; stops are likely to be run.",
                vol,
            )
        )
    elif vol["level"] == "unusually low":
        conds.append(
            _c(
                "volatility",
                WAIT,
                f"ATR percentile {vol['atr_percentile_same_time']}: unusually low; targets may not be reached.",
                vol,
                confirm="volatility returning to a normal range",
            )
        )
    else:
        conds.append(
            _c(
                "volatility",
                PASS,
                f"Volatility {vol['level']} (ATR percentile {vol['atr_percentile_same_time']} "
                f"for this time, state {vol['state']}).",
                vol,
            )
        )

    # 9. Session quality
    nxt = next_eligible_window(symbol, now, style)
    kz = items.get("kill_zone") or {}
    lunch = items.get("outside_lunch") or {}
    if not market.get("open"):
        conds.append(_c("session", FAIL, f"Market closed ({market.get('reason')}).", {"next_eligible_window": nxt}))
    elif kz.get("status") == PASS and lunch.get("status") != FAIL:
        conds.append(_c("session", PASS, kz.get("reason", ""), {"window": checklist.get("window")}))
    else:
        conds.append(
            _c(
                "session",
                WAIT,
                (lunch.get("reason") if lunch.get("status") == FAIL else kz.get("reason"))
                or "Outside the preferred windows.",
                {"next_eligible_window": nxt, "window": checklist.get("window")},
                confirm=f"the next eligible window at {nxt['start_et']} ET" if nxt else "a preferred window",
            )
        )

    # 10. News
    ns = news.get("status")
    if ns == "clear":
        conds.append(_c("news", PASS, news.get("summary") or "No high-impact event window.", news))
    elif ns == "inside_window":
        conds.append(_c("news", FAIL, news.get("summary") or "Inside a high-impact event window.", news))
    else:
        conds.append(
            _c(
                "news",
                UNKNOWN,
                "NEWS STATUS UNKNOWN: " + (news.get("summary") or "no economic calendar."),
                news,
                confirm="an imported economic calendar showing no high-impact event",
            )
        )

    # 11. Reward:risk (net of costs and slippage) on T1
    levels = None
    if signal and signal.get("targets"):
        s = 1 if direction == "long" else -1
        entry, stop = _rnd(float(signal["entry"]), tick), _rnd(float(signal["stop"]), tick)
        tg = [_rnd(float(x), tick) for x in signal["targets"]]
        rr = reward_risk(symbol, direction, entry, stop, tg[0], cfg)
        rr2 = reward_risk(symbol, direction, entry, stop, tg[1], cfg) if len(tg) > 1 else None
        ext = next(
            (
                lv
                for lv in sorted(liquidity, key=lambda x: (x["price"] - entry) * s)
                if (lv["price"] - (tg[-1])) * s > 0
            ),
            None,
        )
        if not rr["valid"]:
            conds.append(_c("reward_risk", FAIL, rr["reason"], rr))
        else:
            ok = rr["ratio"] is not None and rr["ratio"] >= MIN_RR
            conds.append(
                _c(
                    "reward_risk",
                    PASS if ok else FAIL,
                    f"Net {rr['ratio']}:1 to T1 after ${rr['costs']} costs "
                    f"and slippage per contract; minimum {MIN_RR}:1.",
                    rr,
                )
            )
        slip = cfg.slippage_ticks_per_side * tick
        levels = {
            "direction": direction,
            "entry_zone": [_rnd(float(signal["entry_low"]), tick), _rnd(float(signal["entry_high"]), tick)],
            "entry": entry,
            "entry_with_slippage": _rnd(entry + s * slip, tick),
            "trigger": f"{signal['strategy_id']} on the 5m bar of {signal.get('detected_at_et')}",
            "stop": stop,
            "t1": tg[0],
            "t2": tg[1] if len(tg) > 1 else None,
            "extended_target": _rnd(ext["price"], tick) if ext else None,
            "extended_basis": ext.get("name") if ext else None,
            "stop_points": round(abs(entry - stop), 6),
            "stop_ticks": round(abs(entry - stop) / tick),
            "loss_per_contract": rr.get("net_risk"),
            "reward_per_contract_t1": rr.get("net_reward"),
            "reward_per_contract_t2": rr2.get("net_reward") if rr2 and rr2.get("valid") else None,
            "rr_t1": rr.get("ratio"),
            "rr_t2": rr2.get("ratio") if rr2 and rr2.get("valid") else None,
            "point_value": pv,
            "tick_size": tick,
            "costs_per_contract": rr.get("costs"),
            "invalidation_price": stop,
            "expires_at": expiry.isoformat() if expiry else None,
            "basis": "Signal levels from the strategy's market-structure rules, rounded to the tick; loss and reward "
            "include round-trip commission and slippage.",
        }
    else:
        conds.append(_c("reward_risk", UNKNOWN, "No setup with a structural stop and target; levels are not invented."))

    # 12. Apex risk eligibility (Risk Guardian)
    rc = risk_check
    if rc is None:
        rk = readiness.get("risk") or {}
        conds.append(
            _c(
                "risk",
                UNKNOWN,
                "No setup levels to check yet."
                if rk.get("known")
                else "Account snapshot missing: risk status UNKNOWN.",
                rk,
            )
        )
    elif rc.get("error"):
        conds.append(_c("risk", FAIL, rc["error"], rc))
    elif not rc.get("known") or rc.get("stale"):
        conds.append(
            _c(
                "risk",
                UNKNOWN,
                "Account snapshot "
                + ("stale" if rc.get("stale") else "missing")
                + ": risk status UNKNOWN. Enter or sync the account before trading.",
                rc,
                confirm="a fresh account snapshot",
            )
        )
    elif rc.get("verdict") == "violation" or (rc.get("max_size") is not None and rc["max_size"] < 1):
        why = "; ".join(rc.get("violations") or []) or "No contract fits within the remaining risk allowance."
        conds.append(_c("risk", FAIL, why, rc))
    else:
        note = "" if rc.get("rules_verified") else " Rule profile UNVERIFIED: no compliance claim is made."
        conds.append(
            _c(
                "risk",
                PASS,
                f"No violation of the configured values at 1 contract; maximum size {rc.get('max_size')}.{note}",
                rc,
            )
        )

    # 15. Historical performance (optional by default)
    h = history or {}
    if not h.get("available"):
        conds.append(
            _c(
                "history",
                UNKNOWN,
                h.get("reason") or "No completed backtest on stored data for this strategy and instrument.",
                h,
            )
        )
    else:
        oos = h.get("out_of_sample") or {}
        n, exp = oos.get("trades") or 0, oos.get("expectancy")
        if n < 30:
            conds.append(_c("history", UNKNOWN, f"Only {n} out-of-sample trades; at least 30 needed.", h))
        elif exp is not None and exp > 0:
            conds.append(
                _c("history", PASS, f"Out-of-sample expectancy ${exp} over {n} trades ({h.get('data_class')}).", h)
            )
        else:
            conds.append(_c("history", FAIL, f"Out-of-sample expectancy ${exp} over {n} trades: not positive.", h))

    # Order as declared and classify
    order = {k: i for i, (k, _) in enumerate(CONDITIONS)}
    conds.sort(key=lambda c: order[c["key"]])
    strategy_id = signal.get("strategy_id") if signal else None
    mand = mandatory_for(strategy_id)
    for c in conds:
        c["mandatory"] = c["key"] in mand
    by = {c["key"]: c for c in conds}
    blockers = [c for c in conds if c["mandatory"] and c["status"] == FAIL and c["key"] in HARD_FAIL]
    if ds == "UNAVAILABLE" or not bars:
        state = DATA_UNAVAILABLE
    elif not market.get("open") or blockers:
        state = NO_TRADE
    elif signal and all(by[k]["status"] == PASS for k in mand):
        state = LONG_READY if direction == "long" else SHORT_READY
    else:
        state = WAIT_STATE
    missing = [
        {
            "key": c["key"],
            "name": c["name"],
            "status": c["status"],
            "reason": c["reason"],
            "would_confirm": c.get("confirm"),
        }
        for c in conds
        if c["mandatory"] and c["status"] != PASS
    ]
    passed = sum(1 for c in conds if c["status"] == PASS)
    rc = risk_check or {}
    summary = {
        LONG_READY: "All mandatory conditions pass for a long. Review the levels and the risk check before acting.",
        SHORT_READY: "All mandatory conditions pass for a short. Review the levels and the risk check before acting.",
        NO_TRADE: "; ".join(c["reason"] for c in blockers) or f"Market closed ({market.get('reason')}).",
        DATA_UNAVAILABLE: data.get("reason") or "No market data.",
        WAIT_STATE: f"{len(missing)} mandatory condition(s) not yet met: " + ", ".join(m["name"] for m in missing[:4]),
    }[state]
    return {
        "symbol": symbol,
        "state": state,
        "summary": summary,
        "strategy_id": strategy_id,
        "direction": direction,
        "data_class": ds,
        "data_timestamp": bars[-1].ts.isoformat() if bars else None,
        "as_of": now.isoformat(),
        "ny_time": now.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "trading_date": trading_day(now).isoformat(),
        "conditions": conds,
        "mandatory": sorted(mand),
        "passed": passed,
        "total": len(conds),
        "missing": missing,
        "levels": levels if state not in (DATA_UNAVAILABLE,) else None,
        "max_size": rc.get("max_size"),
        "apex_compliance": "NO VIOLATION FOUND (verified rules)"
        if rc.get("may_be_called_compliant")
        else "NOT CONFIRMED" + (" - rule profile unverified" if rc and not rc.get("rules_verified") else ""),
        "news_status": "NEWS STATUS UNKNOWN" if by["news"]["status"] == UNKNOWN else ns.upper() if ns else None,
        "next_eligible_window": nxt,
        "regime": regime,
        "volatility": volatility,
        "expires_at": expiry.isoformat() if expiry else None,
        "disclaimer": "Analysis only: PROJECT APEX never submits, modifies or cancels orders. READY means the "
        "configured conditions are met, not that the trade will be profitable.",
    }


# --- async gathering ----------------------------------------------------------------------------


async def latest_history(session: Any, strategy_id: str | None, symbol: str) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.research import BacktestRun

    if not strategy_id:
        return {"available": False, "reason": "No active strategy."}
    run = await session.scalar(
        select(BacktestRun)
        .where(BacktestRun.strategy_id == strategy_id, BacktestRun.symbol == symbol, BacktestRun.status == "done")
        .order_by(BacktestRun.finished_at.desc())
        .limit(1)
    )
    if run is None or not run.result:
        return {"available": False, "reason": f"No completed backtest for {strategy_id} on {symbol}."}
    res = run.result
    dc = res.get("data_class") or run.provider
    if dc == "simulated" or run.provider == "simulated":
        return {
            "available": False,
            "reason": "Only a SIMULATED-data backtest exists; it says nothing about the real market.",
            "run_id": run.id,
            "data_class": "SIMULATED",
        }
    return {
        "available": True,
        "run_id": run.id,
        "data_class": str(dc).upper(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "in_sample": res.get("in_sample"),
        "out_of_sample": res.get("out_of_sample"),
        "segments": res.get("segments"),
    }


async def confirmation(
    session: Any, symbol: str, now: datetime | None = None, style: str = "intraday"
) -> dict[str, Any]:
    from decimal import Decimal

    from app.api.routes.account import cost_model_for
    from app.api.routes.smc import _analyses
    from app.core.config import get_settings
    from app.research.scanner import load_bars
    from app.risk.engine import check_trade
    from app.services.account import current_state
    from app.services.market_intel import regime_profile, volatility_profile
    from app.services.readiness import HISTORY_DAYS, active_signal, readiness, signal_setup

    s = get_settings()
    live = now is None
    now = now or datetime.now(UTC)
    rd = await readiness(session, symbol, style=style, now=now, use_cache=live)
    provider = s.market_data_provider
    bars = (
        await load_bars(session, provider, symbol, now - timedelta(days=HISTORY_DAYS), now)
        if provider != "none"
        else []
    )
    sig = await active_signal(session, provider, symbol, now) if provider != "none" else None
    setup = signal_setup(sig)
    risk_check: dict[str, Any] | None = None
    if setup and setup.get("targets"):
        inst = INSTRUMENTS[symbol]
        tick = Decimal(str(inst.tick_size))
        q = lambda x: (Decimal(str(x)) / tick).to_integral_value() * tick  # noqa: E731
        rules, st = await current_state(session, now)
        try:
            c = check_trade(
                rules,
                st,
                inst,
                setup["direction"],
                q(setup["entry"]),
                q(setup["stop"]),
                q(setup["targets"][0]),
                1,
                cost_model_for(symbol),
                INSTRUMENTS,
                Decimal(str(s.risk_max_trade_fraction)),
            )
            risk_check = {
                "known": st.known,
                "stale": st.stale,
                "rules_verified": rules.verified,
                "verdict": c.verdict,
                "max_size": c.max_size,
                "max_size_by_risk": c.max_size_by_risk,
                "max_size_by_contract_limit": c.max_size_by_contract_limit,
                "loss_after_costs_1": float(c.loss_after_costs),
                "violations": list(c.violations),
                "warnings": list(c.warnings),
                "may_be_called_compliant": c.may_be_called_compliant,
                "summary": c.summary,
            }
        except ValueError as exc:
            risk_check = {"error": f"Risk check rejected the levels: {exc}", "known": st.known}
    analyses = await _analyses(session, symbol, now) if bars else {}
    zones = [p.as_dict() for a in (analyses.get("5m"), analyses.get("15m")) if a for p in a.patterns]
    liquidity = [
        {**lv, "name": f"{tf} {lv.get('name')}"}
        for tf in ("15m", "1h")
        if analyses.get(tf)
        for lv in analyses[tf].liquidity
    ]
    vol = volatility_profile(bars, now) if bars else None
    a5 = analyses.get("5m")
    recent_choch = bool(a5 and any(p.type in ("CHoCH", "MSS") for p in a5.patterns[-6:]))
    rvol = ((rd.get("checklist") or {}).get("relative_volume") or {}).get("ratio")
    crosses = ((rd.get("checklist") or {}).get("regime") or {}).get("vwap_crosses_today")
    regime = regime_profile(bars, now, a5.trend if a5 else None, recent_choch, crosses, rvol) if bars else None
    hist = await latest_history(session, setup.get("strategy_id") if setup else None, symbol)
    out = decide(symbol, now, bars, rd, setup, risk_check, zones, liquidity, hist, vol, regime, style=style)
    out["contract"] = ((await _feed_contracts()).get(symbol) or {}).get("contract")
    out["provider"] = provider
    return out


async def _feed_contracts() -> dict[str, Any]:
    from app.marketdata.status import read_feed_status

    return (await read_feed_status() or {}).get("contracts") or {}
