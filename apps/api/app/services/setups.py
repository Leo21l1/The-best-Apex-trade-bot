"""Five-factor confluence, structural trade levels and technical setup confirmation (pure functions).

A TECHNICAL BUY/SELL SETUP CONFIRMED means a strategy's predefined technical rules are satisfied on
closed candles. It is NOT a prediction, NOT a probability and NOT proof of profitability: historical
evidence is reported separately and unvalidated strategies stay labelled RESEARCH / UNVALIDATED.

Five factors per direction, each PASS / FAIL / UNKNOWN (UNKNOWN never counts as PASS):
  1 Higher-timeframe bias   - 15m swing + 1h structure, daily context, premium/discount location
  2 Market structure        - fresh confirmed internal BOS/CHoCH on 5m/1m in the setup direction
  3 Liquidity               - opposing liquidity swept and reclaimed, or acceptance beyond liquidity
  4 FVG / order block       - price retesting a still-valid zone of the setup direction
  5 Momentum / execution    - VWAP side, confirming 5m candle, volatility/volume, session
A market event is counted once: a zone created by the same candle that broke structure is not also a
"retest" (factor 4 needs the zone to exist before the current bar and to be revisited).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.services.checklist import ChecklistConfig, reward_risk

FACTORS = {
    1: "Higher-timeframe bias",
    2: "Market structure",
    3: "Liquidity",
    4: "FVG / order block",
    5: "Momentum / execution",
}
#: Strategy-specific confirmations that must PASS regardless of the total score.
MANDATORY: dict[str, tuple[int, ...]] = {
    "mtf_trend": (1, 5),
    "vwap_trend": (1, 5),
    "momentum_pullback": (1, 2),
    "orb": (5,),
    "breakout_retest": (2, 5),
    "vol_expansion": (5,),
    "sweep_reversal": (3,),
    "mean_reversion": (3,),
    "smc_sweep_mss": (2, 3),
    "smc_ob_retest": (2, 4),
    "smc_fvg_retest": (4,),
}
#: Bumped whenever factor or level rules change, and stored with every recorded signal.
ENGINE_VERSION = "setups-1.1"
STATES = (
    "WAIT",
    "DEVELOPING BUY",
    "DEVELOPING SELL",
    "TECHNICAL BUY SETUP CONFIRMED",
    "TECHNICAL SELL SETUP CONFIRMED",
    "INVALIDATED",
    "EXPIRED",
    "MISSED",
)
FRESH_DATA = ("LIVE", "SIMULATED")
NEWS_BLOCK = ("NEWS WINDOW ACTIVE", "POST-NEWS VOLATILITY")


@dataclass(frozen=True)
class SetupConfig:
    min_confluence: int = 4  # 4 or 5; 3/5 is recorded for research only
    min_net_rr: float = 1.5
    expiry_minutes: int = 30
    recent_minutes: int = 60  # structure / liquidity events count as "fresh" for this long
    zone_tol_atr: float = 0.25
    stop_buffer_atr: float = 0.1
    min_risk_atr: float = 0.3
    missed_r: float = 0.5  # price this many R beyond the entry zone (toward the target) = MISSED
    risk_warning_usd: float = 1000.0  # 1-contract loss at the stop above this is flagged (never altered)
    costs: ChecklistConfig = field(default_factory=ChecklistConfig)


def _f(n: int, status: str, detail: str, event: str | None = None) -> dict[str, Any]:
    return {"n": n, "name": FACTORS[n], "status": status, "detail": detail, "event": event}


def _recent(iso: str | None, now: datetime, minutes: int) -> bool:
    return bool(iso) and datetime.fromisoformat(iso) >= now - timedelta(minutes=minutes)  # type: ignore[arg-type]


def _same_break(lv: dict[str, Any], structure: dict[str, Any], tick: float) -> bool:
    """True when a liquidity acceptance and the factor-2 structure break are the same market event: the
    same level (within 2 ticks) broken within 15 minutes. One event must not count twice."""
    if structure["status"] != "PASS" or structure.get("level") is None or not lv.get("state_at"):
        return False
    dt = abs((datetime.fromisoformat(lv["state_at"]) - datetime.fromisoformat(structure["event_time"])).total_seconds())
    return abs(lv["price"] - structure["level"]) <= 2 * tick and dt <= 900


def factors(doc: dict[str, Any], d: str, cfg: SetupConfig) -> list[dict[str, Any]]:
    """The five factors for direction ``d`` ('bullish' | 'bearish') from an analyzer document."""
    c = doc.get("context") or {}
    now = datetime.fromisoformat(doc["as_of"])
    long = d == "bullish"
    out: list[dict[str, Any]] = []

    # 1 -- higher-timeframe bias (15m swing, 1h, daily, location)
    htf, mid, bias_dir = c.get("htf"), c.get("mid"), c.get("bias_dir")
    pd15 = c.get("pd15")
    daily = c.get("daily") or {}
    dtxt = f" Daily: last completed session closed {daily['trend']}." if daily.get("trend") else ""
    if bias_dir == "wait" or (htf in (None, "unknown") and mid in (None, "unknown")):
        out.append(_f(1, "UNKNOWN", "Not enough confirmed 1h/15m structure." + dtxt))
    elif bias_dir != d:
        out.append(_f(1, "FAIL", f"Bias is {bias_dir} (1h {htf}, 15m {mid}), not {d}." + dtxt))
    elif pd15 and pd15["zone"] in (("premium", "above range") if long else ("discount", "below range")):
        out.append(
            _f(
                1,
                "FAIL",
                f"Bias is {d}, but price is in the {pd15['zone']} of the 15m dealing range "
                f"({pd15['position']:.0%}) - poor location for a {'long' if long else 'short'}." + dtxt,
            )
        )
    else:
        loc = f"; price in {pd15['zone']} ({pd15['position']:.0%})" if pd15 else ""
        out.append(_f(1, "PASS", f"1h {htf} and 15m {mid} favour {d}{loc}." + dtxt))

    # 2 -- market structure (fresh confirmed internal shift in the direction)
    short = c.get("short")
    ev = None
    for tf in ("5m", "1m"):
        for e in reversed((((doc.get("advanced") or {}).get(tf) or {}).get("internal") or {}).get("events") or []):
            if e["direction"] == d and _recent(e["time"], now, cfg.recent_minutes):
                ev = {**e, "tf": tf}
                break
        if ev:
            break
    if short in (None, "unknown"):
        out.append(_f(2, "UNKNOWN", "5m internal structure unavailable."))
    elif short not in (d, "neutral") and not ev:
        out.append(_f(2, "FAIL", f"5m internal structure is {short} (contradicts a {d} setup)."))
    elif ev and short == d:
        out.append(
            _f(
                2,
                "PASS",
                f"Confirmed {d} {ev['kind']} on {ev['tf']} through {ev['level']:g} "
                f"(candle {ev['time'][11:16]} UTC); 5m internal structure {short}.",
                f"structure:{ev['tf']}:{ev['time']}",
            )
        )
        out[-1].update(level=ev["level"], event_time=ev["time"])
    else:
        out.append(
            _f(
                2,
                "FAIL",
                f"Developing: no fresh confirmed {d} structure shift in the last "
                f"{cfg.recent_minutes} min (5m internal {short}).",
            )
        )

    # 3 -- liquidity
    liq = [lv for lv in doc.get("levels", []) if lv.get("type") == "liquidity"]
    if not liq:
        out.append(_f(3, "UNKNOWN", "No liquidity levels detected from the available data."))
    else:
        want_sweep = "sell-side" if long else "buy-side"
        accept_side = "buy-side" if long else "sell-side"
        sw = [
            lv
            for lv in liq
            if lv["side"] == want_sweep
            and lv["state"] == "swept and reclaimed"
            and _recent(lv.get("state_at"), now, cfg.recent_minutes)
        ]
        acc = [
            lv
            for lv in liq
            if lv["side"] == accept_side
            and lv["state"] == "accepted beyond"
            and _recent(lv.get("state_at"), now, cfg.recent_minutes)
        ]
        against = [
            lv
            for lv in liq
            if lv["side"] == accept_side
            and lv["state"] == "swept and reclaimed"
            and _recent(lv.get("state_at"), now, cfg.recent_minutes)
        ]
        if sw:
            lv = sw[-1]
            out.append(
                _f(
                    3,
                    "PASS",
                    f"{want_sweep.capitalize()} liquidity at {lv['label']} {lv['price']:g} swept "
                    "and reclaimed (failed breakout).",
                    f"liquidity:{lv['label']}:{lv.get('state_at')}",
                )
            )
        elif acc and not against and _same_break(acc[-1], out[1], doc["tick_size"]):
            lv = acc[-1]
            out.append(
                _f(
                    3,
                    "FAIL",
                    f"Acceptance beyond {lv['label']} {lv['price']:g} is the same close that broke "
                    "structure (already counted under market structure).",
                )
            )
        elif acc and not against:
            lv = acc[-1]
            out.append(
                _f(
                    3,
                    "PASS",
                    f"Price accepted beyond {accept_side} liquidity at {lv['label']} {lv['price']:g} "
                    "(breakout holding).",
                    f"liquidity:{lv['label']}:{lv.get('state_at')}",
                )
            )
        elif against:
            lv = against[-1]
            out.append(
                _f(
                    3,
                    "FAIL",
                    f"{accept_side.capitalize()} liquidity at {lv['label']} {lv['price']:g} was swept "
                    f"and rejected - opposes a {d} setup.",
                )
            )
        else:
            out.append(
                _f(3, "FAIL", f"No recent {want_sweep} sweep-and-reclaim or acceptance beyond {accept_side} liquidity.")
            )

    # 4 -- FVG / order block retest (not the same event as factor 2)
    price, atr5 = c.get("price"), c.get("atr5")
    zones = []
    for tf in ("5m", "15m"):
        a = (doc.get("advanced") or {}).get(tf) or {}
        zones += [{**z, "tf": tf} for z in (a.get("fvgs") or []) + (a.get("order_blocks") or [])]
    if price is None or not atr5:
        out.append(_f(4, "UNKNOWN", "No price/volatility to evaluate zones."))
    else:
        tol = cfg.zone_tol_atr * atr5
        last_bar = c.get("last_5m_open")
        hit = None
        for z in sorted(zones, key=lambda z: z["confirmed_at"], reverse=True):
            if z["direction"] != d or z["status"] in ("invalidated", "fully mitigated"):
                continue
            if not (z["bottom"] - tol <= price <= z["top"] + tol):
                continue
            if last_bar and z["confirmed_at"] >= last_bar:
                continue  # formed on the current bar: that is the break itself, not a retest
            if out[1]["event"] and z["confirmed_at"][:16] == (out[1]["event"].split(":", 2)[2])[:16]:
                continue  # same candle as the structure break: counted once (factor 2)
            hit = z
            break
        if hit:
            kind = "FVG" if hit["type"].endswith("fvg") else "order block"
            disp = " with displacement" if (hit.get("flags") or {}).get("displacement") else ""
            out.append(
                _f(
                    4,
                    "PASS",
                    f"Retesting a {hit['status']} {d} {kind}{disp} on {hit['tf']} {hit['bottom']:g}-{hit['top']:g}.",
                    f"zone:{hit['id']}",
                )
            )
        else:
            out.append(_f(4, "FAIL", f"Price is not retesting a valid {d} FVG or order block (5m/15m)."))

    # 5 -- momentum / execution
    vw, vol, sess = c.get("vwap") or {}, c.get("volatility") or {}, c.get("session")
    bars = c.get("m5_last") or []
    if not vw.get("available") or not bars:
        out.append(_f(5, "UNKNOWN", "VWAP or 5m candles unavailable."))
    else:
        b = bars[-1]
        rng = max(b["h"] - b["l"], 1e-9)
        body = (b["c"] - b["o"]) / rng
        wick = ((min(b["o"], b["c"]) - b["l"]) if long else (b["h"] - max(b["o"], b["c"]))) / rng
        candle_ok = (body >= 0.3) if long else (body <= -0.3)
        reject_ok = wick >= 0.5 and ((b["c"] >= b["o"]) if long else (b["c"] <= b["o"]))
        vw_ok = (vw["side"] == "above") if long else (vw["side"] == "below")
        vol_ok = vol.get("state") in ("normal", "expanded") or (vol.get("relative_volume") or 0) >= 1.0
        sess_ok = sess in ("New York morning", "New York afternoon")
        parts = [
            ("VWAP", vw_ok, f"price {vw['side']} VWAP {vw.get('vwap')}"),
            (
                "candle",
                candle_ok or reject_ok,
                "last 5m candle " + ("confirms" if candle_ok else "rejects" if reject_ok else "does not confirm"),
            ),
            ("volatility", vol_ok, f"volatility {vol.get('state')}, rel. volume {vol.get('relative_volume')}"),
            ("session", sess_ok, f"session {sess}"),
        ]
        bad = [p[2] for p in parts if not p[1]]
        txt = "; ".join(p[2] for p in parts)
        out.append(_f(5, "PASS" if not bad else "FAIL", txt if not bad else "Missing: " + "; ".join(bad) + "."))
    return out


def score(fs: list[dict[str, Any]]) -> int:
    return sum(1 for f in fs if f["status"] == "PASS")


def _round(p: float, tick: float, mode: str) -> float:
    q = p / tick
    q = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
    return round(q * tick, 6)


def plan(doc: dict[str, Any], sig: dict[str, Any], fs: list[dict[str, Any]], cfg: SetupConfig) -> dict[str, Any]:
    """Entry zone, structural stop and liquidity/structure targets for one strategy signal."""
    c = doc.get("context") or {}
    tick = doc["tick_size"]
    atr5 = c.get("atr5") or tick * 8
    long = sig["direction"] == "long"
    s = 1 if long else -1
    entry = _round(sig["entry"], tick, "near")
    lo = sig.get("entry_low") or entry - 0.1 * atr5
    hi = sig.get("entry_high") or entry + 0.1 * atr5
    zone = (_round(min(lo, hi), tick, "down"), _round(max(lo, hi), tick, "up"))
    min_risk = max(4 * tick, cfg.min_risk_atr * atr5)
    buffer = max(2 * tick, cfg.stop_buffer_atr * atr5)
    # --- stop: the nearest structural invalidation beyond the entry zone
    cands: list[tuple[float, str]] = []
    for lv in doc.get("levels", []):
        if lv.get("type") == "liquidity" and lv["state"] == "swept and reclaimed" and lv.get("extreme") is not None:
            if (lv["side"] == "sell-side") == long:
                cands.append((lv["extreme"], f"sweep extreme of {lv['label']}"))
    zone_ev = next((f["event"] for f in fs if f["n"] == 4 and f["status"] == "PASS"), None)
    for tf in ("5m", "15m"):
        a = (doc.get("advanced") or {}).get(tf) or {}
        for z in (a.get("fvgs") or []) + (a.get("order_blocks") or []):
            if zone_ev and zone_ev == f"zone:{z['id']}":
                cands.append(
                    (
                        z["invalidation_price"],
                        f"{'FVG' if z['type'].endswith('fvg') else 'order block'} invalidation ({tf})",
                    )
                )
        for p in (a.get("internal") or {}).get("pivots") or []:
            if (p["kind"] == "low") == long:
                cands.append((p["price"], f"{tf} {'swing low' if long else 'swing high'} {p['label']}".strip()))
    edge = zone[0] if long else zone[1]
    valid = [(p, why) for p, why in cands if (edge - p) * s >= min_risk - 1e-9 and (entry - p) * s <= 4 * atr5]
    stop_src = None
    if valid:
        p, stop_src = max(valid, key=lambda x: x[0] * s)  # closest beyond the zone
        stop = _round(p - s * buffer, tick, "down" if long else "up")
    elif (entry - sig["stop"]) * s >= min_risk:
        stop, stop_src = (
            _round(sig["stop"], tick, "down" if long else "up"),
            "strategy stop (no structural level found)",
        )
    else:
        stop = None
    risk = abs(entry - stop) if stop is not None else None
    # --- targets: opposing liquidity, higher-timeframe pivots and opposing zones
    tcands: list[tuple[float, str]] = []
    for lv in doc.get("levels", []):
        if lv.get("type") == "liquidity" and lv["valid"] and (lv["price"] - entry) * s > 0:
            tcands.append((lv["price"], lv["label"]))
        if lv.get("type") == "zone" and (lv["price"] - entry) * s > 0 and lv.get("bottom") is not None:
            opp = "Bearish" if long else "Bullish"
            if lv["label"].startswith(opp):
                tcands.append((lv["bottom"] if long else lv["top"], lv["label"]))
    a15 = (doc.get("advanced") or {}).get("15m") or {}
    for layer in ("internal", "swing"):
        for p in (a15.get(layer) or {}).get("pivots") or []:
            if (p["kind"] == "high") == long and (p["price"] - entry) * s > 0:
                tcands.append((p["price"], f"15m {layer} {'high' if long else 'low'}"))
    tcands.sort(key=lambda x: x[0] * s)
    tps: list[tuple[float, str]] = []
    for p, why in tcands:
        tp = _round(p - s * tick, tick, "down" if long else "up")  # one tick in front of the level
        if (tp - entry) * s <= 0:
            continue
        if tps and risk and (tp - tps[-1][0]) * s < 0.5 * risk:
            continue
        tps.append((tp, why))
        if len(tps) == 2:
            break
    if not tcands and sig.get("targets"):
        tps = [
            (_round(t, tick, "down" if long else "up"), "strategy target (no opposing liquidity identified)")
            for t in sig["targets"][:2]
        ]
    rr = []
    for tp, _ in tps:
        r = (
            reward_risk(doc["symbol"], sig["direction"], entry, stop, tp, cfg.costs)
            if stop is not None
            else {"valid": False}
        )
        rr.append(
            {
                "gross": r.get("gross_ratio"),
                "net": r.get("ratio"),
                "net_reward": r.get("net_reward"),
                "net_risk": r.get("net_risk"),
                "costs": r.get("costs"),
            }
            if r.get("valid")
            else None
        )
    return {
        "entry": entry,
        "entry_zone": list(zone),
        "stop": stop,
        "stop_basis": stop_src,
        "tp1": tps[0][0] if tps else None,
        "tp1_basis": tps[0][1] if tps else None,
        "tp2": tps[1][0] if len(tps) > 1 else None,
        "tp2_basis": tps[1][1] if len(tps) > 1 else None,
        "rr_tp1": rr[0] if rr else None,
        "rr_tp2": rr[1] if len(rr) > 1 else None,
        "risk_points": risk,
        "risk_usd_1c": rr[0]["net_risk"] if rr and rr[0] else None,
        "risk_warning": (
            f"1-contract loss at the stop is ${rr[0]['net_risk']:,.2f}, above the ${cfg.risk_warning_usd:,.0f} "
            "account risk limit: the structural stop was not moved; consider a smaller contract or skip it."
        )
        if rr and rr[0] and rr[0]["net_risk"] > cfg.risk_warning_usd
        else None,
        "note": "Analytical levels, not orders or guaranteed fills.",
    }


def evaluate(
    doc: dict[str, Any], sig: dict[str, Any], evidence: dict[str, Any] | None, cfg: SetupConfig
) -> dict[str, Any]:
    """One strategy signal: factors, mandatory checks, levels, the 12 requirements and the verdict."""
    d = "bullish" if sig["direction"] == "long" else "bearish"
    fs = factors(doc, d, cfg)
    n = score(fs)
    lv = plan(doc, sig, fs, cfg)
    c = doc.get("context") or {}
    now = datetime.fromisoformat(doc["as_of"])
    data = doc.get("data") or {}
    news = doc.get("news") or {}
    mand = MANDATORY.get(sig["strategy_id"], ())
    price = c.get("price")
    s = 1 if d == "bullish" else -1
    created = datetime.fromisoformat(sig["created_at"]) if sig.get("created_at") else now
    beyond = (
        price is not None
        and lv["risk_points"]
        and (price - lv["entry_zone"][1 if s > 0 else 0]) * s > cfg.missed_r * lv["risk_points"]
    )
    req = [
        ("confluence", n >= cfg.min_confluence, f"{n}/5 confluences (need {cfg.min_confluence})"),
        (
            "mandatory",
            all(fs[i - 1]["status"] == "PASS" for i in mand),
            "mandatory: "
            + (", ".join(FACTORS[i] for i in mand) or "strategy trigger only")
            + f" + {sig['strategy_id']} trigger",
        ),
        ("data", data.get("label") in FRESH_DATA, f"market data {data.get('label')}"),
        (
            "contract",
            bool(doc.get("tick_size")) and (data.get("provider") != "databento" or bool(data.get("contract"))),
            f"contract {data.get('contract') or 'n/a'}, tick {doc.get('tick_size')}",
        ),
        (
            "calendar",
            news.get("status") not in (None, "NEWS DATA STALE / UNAVAILABLE"),
            f"calendar {news.get('status') or 'unknown'}",
        ),
        ("news window", news.get("status") not in NEWS_BLOCK, f"news {news.get('status') or 'unknown'}"),
        (
            "entry zone",
            lv["entry_zone"][0] < lv["entry_zone"][1] and not beyond,
            "entry zone valid" if not beyond else "price already ran beyond the entry zone",
        ),
        ("stop", lv["stop"] is not None, lv["stop_basis"] or "no defensible structural stop"),
        ("target", lv["tp1"] is not None, lv["tp1_basis"] or "no realistic target"),
        (
            "net R:R",
            bool(lv["rr_tp1"]) and (lv["rr_tp1"]["net"] or 0) >= cfg.min_net_rr,
            f"net R:R to TP1 {lv['rr_tp1']['net'] if lv['rr_tp1'] else 'n/a'} (min {cfg.min_net_rr})",
        ),
        (
            "structure",
            c.get("short") not in ("bearish" if d == "bullish" else "bullish",),
            f"5m internal structure {c.get('short')}",
        ),
        (
            "timestamp",
            now - created <= timedelta(minutes=cfg.expiry_minutes),
            f"strategy trigger at {sig.get('created_at')}",
        ),
    ]
    confirmed = all(ok for _, ok, _ in req)
    ev_label = evidence_label(evidence)
    return {
        "strategy_id": sig["strategy_id"],
        "direction": sig["direction"],
        "bias": d,
        "signal_bar": sig.get("bar_ts"),
        "trigger_at": sig.get("created_at"),
        "score": n,
        "factors": fs,
        "mandatory": [FACTORS[i] for i in mand],
        "requirements": [{"key": k, "ok": ok, "detail": t} for k, ok, t in req],
        "missing": [t for _, ok, t in req if not ok],
        "confirmed": confirmed,
        "levels": lv,
        "evidence": ev_label,
        "evidence_detail": evidence or {},
    }


def evidence_label(evidence: dict[str, Any] | None) -> str:
    e = (evidence or {}).get("evidence") or ""
    if e.startswith("Validated"):
        return "VALIDATED"
    if e.startswith("Promising"):
        return "PROMISING BUT UNVALIDATED"
    if e.startswith("Negative"):
        return "NEGATIVE EVIDENCE"
    return "INSUFFICIENT EVIDENCE"


def assess(
    doc: dict[str, Any], signals: list[dict[str, Any]], evidences: dict[str, dict[str, Any]], cfg: SetupConfig
) -> dict[str, Any]:
    """Live state for the instrument: best confirmed candidate, else the best developing direction."""
    cands = [evaluate(doc, s, evidences.get(s["strategy_id"]), cfg) for s in signals]
    conf = sorted(
        (x for x in cands if x["confirmed"]),
        key=lambda x: (x["score"], (x["levels"]["rr_tp1"] or {}).get("net") or 0),
        reverse=True,
    )
    dir_f = {d: factors(doc, d, cfg) for d in ("bullish", "bearish")}
    dir_score = {d: score(f) for d, f in dir_f.items()}
    if conf:
        best = conf[0]
        state = f"TECHNICAL {'BUY' if best['direction'] == 'long' else 'SELL'} SETUP CONFIRMED"
        return {
            "state": state,
            "best": best,
            "candidates": cands,
            "direction_scores": dir_score,
            "factors": best["factors"],
        }
    pend = sorted(cands, key=lambda x: x["score"], reverse=True)
    if pend and pend[0]["score"] >= 3:
        b = pend[0]
        return {
            "state": f"DEVELOPING {'BUY' if b['direction'] == 'long' else 'SELL'}",
            "best": b,
            "candidates": cands,
            "direction_scores": dir_score,
            "factors": b["factors"],
        }
    d = max(dir_score, key=lambda k: dir_score[k])
    if dir_score[d] >= 3 and dir_score[d] > dir_score["bearish" if d == "bullish" else "bullish"]:
        return {
            "state": f"DEVELOPING {'BUY' if d == 'bullish' else 'SELL'}",
            "best": None,
            "candidates": cands,
            "direction_scores": dir_score,
            "factors": dir_f[d],
            "note": "Confluences are building but no strategy has triggered yet.",
        }
    return {"state": "WAIT", "best": None, "candidates": cands, "direction_scores": dir_score, "factors": dir_f[d]}
