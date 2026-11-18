"""Trading Readiness Checklist: eight calculated conditions, each PASS / FAIL / WAIT / UNKNOWN.

Pure: ``evaluate`` uses only bars that closed at or before ``now`` (completed candles), so the same
function evaluates the checklist "as of" a past trade entry without look-ahead.

Definitions (all thresholds configurable in ``ChecklistConfig``):

A  Kill zone        - now (New York) inside a preferred window for the selected trading style.
B  Not choppy       - confirmed price/VWAP crosses on completed 5-minute bars over the last
                      ``chop_lookback`` bars < ``chop_max_crosses``. A cross is confirmed only when
                      a bar CLOSES beyond VWAP by more than a buffer (max(1 tick, 0.1 x ATR14)),
                      so intrabar flicker and closes hugging VWAP are not counted. Efficiency ratio,
                      ATR and range compression are reported as separate evidence.
C  Above-avg volume - relative volume (time-of-day normalised, see session_intel) >= ``min_rvol``.
D  Outside lunch    - outside the configured lunch window, or inside it with an evidence-based
                      exception (relative volume and volatility both elevated).
E  Setup detected   - an active signal from the existing strategy engine (no signal -> WAIT).
F  Alignment        - market structure trend on completed 1m/5m/15m/1h candles; conflicts are
                      reported as conflicts, not resolved by assumption.
G  Reward:risk      - net of commissions and slippage from the setup's own entry/stop/target
                      (levels are never manufactured) >= ``min_rr``.
H  VWAP crosses     - confirmed crosses since the 09:30 ET VWAP anchor today (session reset).

These are conditions for context, not a strategy: passing them does not imply positive expectancy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate, closed_only
from app.research.indicators import atr, is_rth, session_vwap
from app.research.smc import analyze
from app.services.session_intel import (
    SessionConfig,
    efficiency_ratio,
    market_status,
    reference_window,
    relative_volatility,
    relative_volume,
)

PASS, FAIL, WAIT, UNKNOWN = "PASS", "FAIL", "WAIT", "UNKNOWN"

STYLE_WINDOWS: dict[str, tuple[tuple[time, time], ...]] = {
    # Preferred windows per style (New York time). Configurable; not guarantees.
    "intraday": ((time(9, 30), time(11, 30)), (time(13, 30), time(15, 30))),
    "scalping": ((time(9, 30), time(11, 0)), (time(14, 0), time(15, 45))),
    "momentum": ((time(9, 30), time(11, 30)), (time(13, 30), time(15, 0))),
    "orb": ((time(9, 45), time(10, 30)),),
    "afternoon": ((time(13, 30), time(15, 30)),),
}


@dataclass(frozen=True)
class ChecklistConfig:
    style: str = "intraday"
    chop_lookback: int = 12  # completed 5-minute bars (1 hour)
    chop_max_crosses: int = 4  # PASS when crosses < this
    cross_buffer_atr: float = 0.1
    min_rvol: float = 1.0
    lunch: tuple[time, time] = (time(11, 30), time(13, 30))
    lunch_exception_rvol: float = 1.3
    lunch_exception_volatility: float = 1.1
    min_rr: float = 1.5  # net R:R to T1; set from APEX_MIN_NET_REWARD_RISK
    commission_per_side: dict[str, float] = field(default_factory=lambda: {"standard": 2.5, "micro": 0.6})
    slippage_ticks_per_side: float = 1.0
    session: SessionConfig = field(default_factory=SessionConfig)


def item(
    key: str, name: str, status: str, reason: str, evidence: dict[str, Any] | None = None, calculation: str = ""
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "status": status,
        "reason": reason,
        "evidence": evidence or {},
        "calculation": calculation,
    }


# --- VWAP crosses -------------------------------------------------------------------------------


def confirmed_vwap_crosses(bars5: Sequence[Bar], tick: float, buffer_atr: float) -> list[dict[str, Any]]:
    """Confirmed crosses of close vs session VWAP on completed 5-minute bars (VWAP resets 09:30 ET).

    The side changes only when a close is beyond VWAP by more than the buffer; a close inside the
    buffer keeps the previous side. The first confirmed side of a session is not a cross.
    """
    vw, _ = session_vwap(bars5)
    a = atr(bars5, 14)
    out: list[dict[str, Any]] = []
    side: int | None = None
    day = None
    for i, b in enumerate(bars5):
        if vw[i] is None:
            continue
        d = trading_day(b.ts)
        if d != day:
            day, side = d, None
        buf = max(tick, buffer_atr * (a[i] or 0.0))
        diff = b.close - vw[i]
        if abs(diff) <= buf:
            continue
        s = 1 if diff > 0 else -1
        if side is not None and s != side:
            out.append(
                {
                    "ts": b.ts.isoformat(),
                    "direction": "up" if s > 0 else "down",
                    "close": b.close,
                    "vwap": round(vw[i], 4),
                    "trading_date": d.isoformat(),
                }
            )
        side = s
    return out


# --- reward:risk --------------------------------------------------------------------------------


def reward_risk(
    symbol: str, direction: str, entry: float, stop: float, target: float, cfg: ChecklistConfig, qty: int = 1
) -> dict[str, Any]:
    """Net reward:risk for one target, with costs charged the way trades are filled.

    Same execution policy as the backtester and paper simulator:
    * entry is a market order: commission + ``slippage_ticks_per_side`` adverse slippage;
    * a stop exit is a market order: commission + slippage again;
    * a target exit is a limit order: commission, no slippage.
    So the net reward (target reached) pays commission round trip + entry slippage, and the net risk
    (stop reached) pays commission round trip + entry and stop slippage. Net R:R = net reward / net risk.
    """
    inst = INSTRUMENTS[symbol]
    pv, tick = float(inst.point_value), float(inst.tick_size)
    s = 1 if direction == "long" else -1
    if (entry - stop) * s <= 0:
        return {"valid": False, "reason": "Stop is not on the losing side of the entry."}
    if (target - entry) * s <= 0:
        return {"valid": False, "reason": "Target is not on the profitable side of the entry."}
    comm = cfg.commission_per_side["micro" if inst.is_micro else "standard"] * 2 * qty
    slip_side = cfg.slippage_ticks_per_side * tick * pv * qty
    gross_reward = abs(target - entry) * pv * qty
    gross_risk = abs(entry - stop) * pv * qty
    reward_costs = comm + slip_side  # commission round trip + entry slippage (limit exit)
    risk_costs = comm + 2 * slip_side  # commission round trip + entry and stop slippage
    reward, risk = gross_reward - reward_costs, gross_risk + risk_costs
    return {
        "valid": True,
        "point_value": pv,
        "gross_reward": round(gross_reward, 2),
        "gross_risk": round(gross_risk, 2),
        "reward_costs": round(reward_costs, 2),
        "costs": round(risk_costs, 2),  # cost if stopped out (the Risk Guardian's loss-after-costs basis)
        "net_reward": round(reward, 2),
        "net_risk": round(risk, 2),
        "ratio": round(reward / risk, 2) if risk > 0 else None,
        "gross_ratio": round(gross_reward / gross_risk, 2),
        "method": "net R:R = (reward - commission - entry slippage) / (risk + commission + entry and stop slippage)",
    }


def rr_label(min_rr: float) -> str:
    """One name for the condition everywhere (checklist, confirmation, copilot)."""
    return f"Net R:R to T1 >= {min_rr:g}:1"


# --- evaluate -----------------------------------------------------------------------------------


def _in_windows(t: time, windows: Sequence[tuple[time, time]]) -> tuple[time, time] | None:
    return next((w for w in windows if w[0] <= t < w[1]), None)


def evaluate(
    symbol: str,
    now: datetime,
    bars_1m: Sequence[Bar],
    cfg: ChecklistConfig = ChecklistConfig(),
    setup: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    news: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tick = float(INSTRUMENTS[symbol].tick_size)
    local = now.astimezone(ET)
    t = local.time()
    bars_1m = [b for b in bars_1m if b.ts + timedelta(minutes=1) <= now]  # completed candles only
    market = market_status(symbol, now)
    items: list[dict[str, Any]] = []

    # A. Kill zone
    windows = STYLE_WINDOWS.get(cfg.style, STYLE_WINDOWS["intraday"])
    wtxt = ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in windows)
    hit = _in_windows(t, windows)
    if not market["open"]:
        items.append(
            item(
                "kill_zone",
                "Kill Zone / Afternoon",
                FAIL,
                f"Market closed: {market['reason']}.",
                {"now_et": f"{t:%H:%M}", "windows": wtxt},
            )
        )
    elif hit:
        items.append(
            item(
                "kill_zone",
                "Kill Zone / Afternoon",
                PASS,
                f"{t:%H:%M} ET is inside the {cfg.style} window {hit[0]:%H:%M}-{hit[1]:%H:%M}.",
                {"now_et": f"{t:%H:%M}", "windows": wtxt},
                "preferred window by style",
            )
        )
    else:
        upcoming = [
            a
            for a, _ in windows
            if 0 < (datetime.combine(local.date(), a, ET).astimezone(UTC) - now.astimezone(UTC)).total_seconds() <= 1800
        ]
        items.append(
            item(
                "kill_zone",
                "Kill Zone / Afternoon",
                WAIT if upcoming else FAIL,
                f"{t:%H:%M} ET is outside the {cfg.style} windows ({wtxt})"
                + (f"; next opens {upcoming[0]:%H:%M}." if upcoming else "."),
                {"now_et": f"{t:%H:%M}", "windows": wtxt},
            )
        )

    # Shared 5-minute data
    bars5 = closed_only(aggregate(bars_1m, Timeframe.M5), Timeframe.M5, now)
    crosses = confirmed_vwap_crosses(bars5, tick, cfg.cross_buffer_atr) if bars5 else []
    today = trading_day(now)
    today_rth = [b for b in bars5 if trading_day(b.ts) == today and is_rth(b.ts)]

    # B. Not choppy
    look = bars5[-cfg.chop_lookback :]
    if len(today_rth) < 3:
        items.append(
            item(
                "not_choppy",
                "Not Choppy (< 4 VWAP crosses)",
                UNKNOWN,
                "VWAP is anchored at 09:30 ET; fewer than 3 completed 5-minute bars since the anchor today.",
                {"er": efficiency_ratio([b.close for b in look]) if look else None},
            )
        )
    else:
        since = look[0].ts if look else now
        n = sum(1 for c in crosses if c["ts"] >= since.isoformat() and c["trading_date"] == today.isoformat())
        er = efficiency_ratio([b.close for b in look])
        a14 = atr(bars5, 14)[-1]
        a50 = atr(bars5, 50)[-1]
        compression = round(a14 / a50, 2) if a14 and a50 else None
        status = PASS if n < cfg.chop_max_crosses else FAIL
        items.append(
            item(
                "not_choppy",
                "Not Choppy (< 4 VWAP crosses)",
                status,
                f"{n} confirmed VWAP crosses in the last {len(look)} completed 5-minute bars "
                f"(threshold < {cfg.chop_max_crosses}).",
                {
                    "crosses": n,
                    "lookback_bars": len(look),
                    "efficiency_ratio": round(er, 2) if er is not None else None,
                    "atr14_5m": round(a14, 4) if a14 else None,
                    "atr_compression_14_50": compression,
                },
                f"cross = completed 5m close beyond VWAP by > max(1 tick, {cfg.cross_buffer_atr} x ATR14)",
            )
        )

    # C. Above-average volume
    rv = relative_volume(bars_1m, now, cfg.session)
    if not rv.get("available"):
        items.append(item("volume", "Above-Average Volume", UNKNOWN, rv.get("reason", "Not available."), rv))
    else:
        ok = rv["ratio"] is not None and rv["ratio"] >= cfg.min_rvol
        items.append(
            item(
                "volume",
                "Above-Average Volume",
                PASS if ok else FAIL,
                f"Relative volume {rv['ratio']} (current {rv['current_volume']} vs reference "
                f"{rv['reference_volume']} for {rv['window_et']} ET); minimum {cfg.min_rvol}.",
                rv,
                rv["method"],
            )
        )

    # D. Outside lunch
    vol = relative_volatility(bars_1m, now, cfg.session)
    lo, hi = cfg.lunch
    if not (lo <= t < hi):
        items.append(
            item("outside_lunch", "Outside Lunch Hours", PASS, f"{t:%H:%M} ET is outside {lo:%H:%M}-{hi:%H:%M}.")
        )
    else:
        exc = bool(
            rv.get("available")
            and (rv.get("ratio") or 0) >= cfg.lunch_exception_rvol
            and vol.get("available")
            and (vol.get("ratio") or 0) >= cfg.lunch_exception_volatility
        )
        items.append(
            item(
                "outside_lunch",
                "Outside Lunch Hours",
                PASS if exc else FAIL,
                (
                    f"Inside lunch ({lo:%H:%M}-{hi:%H:%M}) but participation is elevated: relative volume "
                    f"{rv.get('ratio')}, volatility {vol.get('ratio')} (exception)."
                )
                if exc
                else f"Inside the lunch window {lo:%H:%M}-{hi:%H:%M}; volume/volatility do not justify an exception.",
                {"relative_volume": rv.get("ratio"), "relative_volatility": vol.get("ratio")},
                f"exception if RVOL >= {cfg.lunch_exception_rvol} and volatility >= {cfg.lunch_exception_volatility}",
            )
        )

    # E. Setup detected
    if setup:
        items.append(
            item(
                "setup",
                "Setup Detected",
                PASS,
                f"{setup.get('strategy_id')} {setup.get('direction')} signal at {setup.get('detected_at_et', '?')}.",
                {
                    k: setup.get(k)
                    for k in (
                        "strategy_id",
                        "direction",
                        "entry",
                        "entry_low",
                        "entry_high",
                        "stop",
                        "targets",
                        "conditions",
                    )
                },
            )
        )
    elif data and data.get("status") in ("UNAVAILABLE", "STALE"):
        items.append(item("setup", "Setup Detected", UNKNOWN, "Market data is unavailable or stale."))
    else:
        items.append(
            item("setup", "Setup Detected", WAIT, "No strategy's conditions are met on the latest completed bar.")
        )

    # F. Directional alignment
    trends: dict[str, str] = {}
    for tf in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1):
        b = closed_only(aggregate(bars_1m, tf), tf, now)[-400:]
        trends[tf.value] = analyze(symbol, tf.value, b, tick).trend if len(b) >= 10 else "unknown"
    vals = set(trends.values())
    direction = None
    if "unknown" in vals and len(vals - {"unknown"}) <= 1 and list(trends.values()).count("unknown") >= 2:
        f_status, f_reason = UNKNOWN, "Not enough completed candles on several timeframes."
    elif {"bullish", "bearish"} <= vals:
        f_status, f_reason = FAIL, "Timeframes conflict: " + ", ".join(f"{k} {v}" for k, v in trends.items())
    elif vals in ({"bullish"}, {"bearish"}):
        direction = vals.pop()
        f_status, f_reason = PASS, f"All four timeframes {direction} on completed candles."
    else:
        lean = next((v for v in ("bullish", "bearish") if v in vals), None)
        direction = lean
        f_status, f_reason = WAIT, "Partly aligned (" + ", ".join(f"{k} {v}" for k, v in trends.items()) + ")."
    if setup and direction and f_status != FAIL:
        want = "bullish" if setup.get("direction") == "long" else "bearish"
        if direction != want:
            f_status, f_reason = FAIL, f"Setup is {setup.get('direction')} but structure is {direction}."
    items.append(
        item(
            "alignment",
            "Directional Alignment",
            f_status,
            f_reason,
            {"trends": trends},
            "market-structure trend (BOS/CHoCH) per timeframe, completed candles only",
        )
    )

    # G. Net reward:risk to T1
    rr = None
    label = rr_label(cfg.min_rr)
    if setup and setup.get("stop") is not None and setup.get("targets"):
        rr = reward_risk(
            symbol, setup["direction"], float(setup["entry"]), float(setup["stop"]), float(setup["targets"][0]), cfg
        )
        if not rr["valid"]:
            items.append(item("reward_risk", label, FAIL, rr["reason"], rr))
        else:
            items.append(
                item(
                    "reward_risk",
                    label,
                    PASS if rr["ratio"] >= cfg.min_rr else FAIL,
                    f"Net {rr['ratio']}:1 to T1 (gross {rr['gross_ratio']}:1); ${rr['reward_costs']} costs if the "
                    f"target fills, ${rr['costs']} if stopped. Minimum {cfg.min_rr:g}:1.",
                    rr,
                    rr["method"],
                )
            )
    else:
        items.append(
            item(
                "reward_risk",
                label,
                UNKNOWN,
                "No setup with a justified stop and target; levels are not invented.",
            )
        )

    # H. VWAP crosses today
    today_crosses = [c for c in crosses if c["trading_date"] == today.isoformat()]
    if not today_rth:
        items.append(
            item(
                "vwap_crosses",
                "VWAP Crosses Today",
                UNKNOWN,
                "Session VWAP starts at 09:30 ET; no completed regular-session bars yet today.",
            )
        )
    else:
        n = len(today_crosses)
        st = PASS if n <= 2 else WAIT if n < cfg.chop_max_crosses else FAIL
        items.append(
            item(
                "vwap_crosses",
                "VWAP Crosses Today",
                st,
                f"{n} confirmed crosses since 09:30 ET on 5-minute closes; "
                + (
                    "few crosses support directional setups."
                    if st == PASS
                    else "repeated crossing weakens directional setups."
                ),
                {"count": n, "anchor": "09:30 ET", "timeframe": "5m", "crosses": today_crosses[-6:]},
            )
        )

    # Overall status
    critical: list[str] = []
    if not market["open"]:
        critical.append(f"Market closed ({market['reason']}).")
    if data and data.get("status") in ("STALE", "UNAVAILABLE"):
        critical.append(f"Market data {data['status']}: {data.get('reason')}")
    if news and news.get("status") == "inside_window":
        critical.append(news.get("summary", "High-impact event window."))
    if risk and risk.get("known") and (risk.get("risk_budget") or 0) <= 0:
        critical.append("No remaining risk budget on the entered account figures.")
    by = {i["key"]: i["status"] for i in items}
    passed = sum(1 for i in items if i["status"] == PASS)
    if critical:
        overall = "NO TRADE"
    elif by["setup"] == PASS and FAIL not in (by["not_choppy"], by["alignment"], by["reward_risk"]) and passed >= 6:
        overall = "REVIEW SETUP"
    else:
        overall = "WAIT"
    return {
        "symbol": symbol,
        "as_of": now.isoformat(),
        "ny_time": local.strftime("%Y-%m-%d %H:%M %Z"),
        "style": cfg.style,
        "window": reference_window(now, cfg.session),
        "market": market,
        "items": items,
        "passed": passed,
        "total": len(items),
        "failures": [i["name"] for i in items if i["status"] == FAIL],
        "unknowns": [i["name"] for i in items if i["status"] == UNKNOWN],
        "critical": critical,
        "status": overall,
        "direction": direction,
        "relative_volume": rv,
        "relative_volatility": vol,
        "regime": _regime(bars5, crosses, today),
        "disclaimer": "Readiness context only. Passing conditions does not imply a profitable trade; "
        "order-book liquidity is not known (no depth feed).",
    }


def _regime(bars5: Sequence[Bar], crosses: Sequence[dict[str, Any]], today: Any) -> dict[str, Any]:
    look = bars5[-24:]
    er = efficiency_ratio([b.close for b in look]) if look else None
    n = sum(1 for c in crosses if c["trading_date"] == str(today))
    label = "unknown" if er is None else "trending" if er >= 0.35 else "choppy" if er < 0.15 or n >= 4 else "ranging"
    return {
        "label": label,
        "efficiency_ratio_2h": round(er, 2) if er is not None else None,
        "vwap_crosses_today": n,
        "definition": "Efficiency ratio of 5m closes over 2 hours: >= 0.35 trending, < 0.15 (or >= 4 VWAP crosses) "
        "choppy, otherwise ranging.",
    }
