"""Five-factor confluence, mandatory triggers, structural levels, confirmation gates, lifecycle, history."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.db import get_sessionmaker
from app.models.signal import SetupSignal
from app.research.bars import Bar
from app.services import setups, signal_tracker
from app.services.checklist import ChecklistConfig

NOW = datetime(2026, 11, 18, 15, 30, tzinfo=UTC)  # Wednesday 10:30 ET
CFG = setups.SetupConfig(
    costs=ChecklistConfig(commission_per_side={"standard": 2.5, "micro": 0.6}, slippage_ticks_per_side=1)
)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def scenario(sell: bool = False) -> dict[str, Any]:
    """NQ: bullish 1h/15m, discount, sell-side sweep at PDL, 5m bullish CHoCH, FVG retest, momentum.

    ``sell=True`` mirrors every price around 21000 and every direction, so a SELL must behave identically.
    """

    def p(x: float) -> float:
        return 42000 - x if sell else x

    up = "bearish" if sell else "bullish"
    hi, lo = ("low", "high") if sell else ("high", "low")
    bar = {"t": iso(5), "o": p(20996), "h": p(21003), "l": p(20995), "c": p(21002)}
    if sell:
        bar["h"], bar["l"] = bar["l"], bar["h"]
    zone = {
        "id": "z1",
        "type": f"{up}_fvg",
        "direction": up,
        "layer": "",
        "formed_at": iso(35),
        "confirmed_at": iso(30),
        "top": max(p(20995), p(21002)),
        "bottom": min(p(20995), p(21002)),
        "status": "unmitigated",
        "invalidation_price": p(20995),
        "flags": {"displacement": True},
    }
    return {
        "symbol": "NQ",
        "tick_size": 0.25,
        "as_of": NOW.isoformat(),
        "data": {
            "label": "LIVE",
            "provider": "databento",
            "contract": "NQZ6",
            "quality": "live",
            "price_time": NOW.isoformat(),
        },
        "news": {"status": "NO MAJOR SCHEDULED EVENT NEARBY", "summary": "clear"},
        "context": {
            "price": p(21000),
            "atr5": 10.0,
            "htf": up,
            "htf_basis": "swing",
            "mid": up,
            "short": up,
            "bias_dir": up,
            "pd15": {"zone": "premium" if sell else "discount", "position": 0.7 if sell else 0.3},
            "daily": {"trend": "lower" if sell else "higher"},
            "vwap": {"available": True, "side": "below" if sell else "above", "vwap": p(20990)},
            "volatility": {"state": "normal", "relative_volume": 1.2},
            "session": "New York morning",
            "last_5m_open": iso(5),
            "m5_last": [bar],
        },
        "advanced": {
            "5m": {
                "internal": {
                    "events": [
                        {
                            "time": iso(10),
                            "direction": up,
                            "kind": "CHoCH",
                            "level": p(20995),
                            "layer": "internal",
                            "pivot_time": iso(40),
                        }
                    ],
                    "pivots": [{"kind": lo, "price": p(20975), "label": "HL", "time": iso(50)}],
                },
                "fvgs": [zone],
                "order_blocks": [],
            },
            "15m": {
                "internal": {"events": [], "pivots": [{"kind": hi, "price": p(21060), "label": "HH"}]},
                "swing": {"pivots": []},
                "fvgs": [],
                "order_blocks": [],
            },
        },
        "levels": [
            {
                "type": "liquidity",
                "label": "Previous day low" if not sell else "Previous day high",
                "price": p(20980),
                "side": "buy-side" if sell else "sell-side",
                "state": "swept and reclaimed",
                "state_at": iso(15),
                "extreme": p(20972),
                "valid": False,
                "timeframe": "1d",
            },
            {
                "type": "liquidity",
                "label": "Overnight high" if not sell else "Overnight low",
                "price": p(21040),
                "side": "sell-side" if sell else "buy-side",
                "state": "untouched",
                "valid": True,
                "timeframe": "session",
            },
        ],
    }


def signal(
    strategy: str = "smc_sweep_mss", sell: bool = False, created_min_ago: float = 2, **kw: Any
) -> dict[str, Any]:
    p = (lambda x: 42000 - x) if sell else (lambda x: x)
    s = {
        "strategy_id": strategy,
        "direction": "short" if sell else "long",
        "entry": p(21000),
        "entry_low": min(p(20998), p(21002)),
        "entry_high": max(p(20998), p(21002)),
        "stop": p(20970),
        "targets": [p(21040), p(21060)],
        "bar_ts": iso(created_min_ago + 5),
        "created_at": iso(created_min_ago),
    }
    return {**s, **kw}


# -------------------------------------------------------------------------------- confirmation


@pytest.mark.parametrize("sell", [False, True])
def test_full_confluence_confirms_with_structural_levels(sell: bool) -> None:
    ev = setups.evaluate(scenario(sell), signal(sell=sell), None, CFG)
    assert [f["status"] for f in ev["factors"]] == ["PASS"] * 5 and ev["score"] == 5
    assert ev["confirmed"], ev["missing"]
    lv = ev["levels"]
    # Stop: nearest structural invalidation beyond the zone (FVG at 20995) minus a 1.0 buffer, tick-rounded.
    assert lv["stop"] == (42000 - 20994 if sell else 20994) and "FVG invalidation" in lv["stop_basis"]
    # TP1 one tick in front of the nearest opposing liquidity; TP2 the 15m swing; never invented further away.
    assert lv["tp1"] == (42000 - 21039.75 if sell else 21039.75) and "Overnight" in lv["tp1_basis"]
    assert lv["tp2"] == (42000 - 21059.75 if sell else 21059.75)
    # NQ $20/pt: reward 39.75 pts = $795 - $5 commission - $5 entry slippage; risk 6 pts = $120 + $5 + $10.
    assert lv["rr_tp1"]["net"] == round(785 / 135, 2) and lv["rr_tp1"]["gross"] == round(39.75 / 6, 2)
    assert ev["evidence"] == "INSUFFICIENT EVIDENCE"
    st = setups.assess(scenario(sell), [signal(sell=sell)], {}, CFG)
    assert st["state"] == f"TECHNICAL {'SELL' if sell else 'BUY'} SETUP CONFIRMED"


def test_mandatory_trigger_beats_the_score() -> None:
    d = scenario()
    d["levels"][0]["state"] = "untouched"  # no sweep -> liquidity FAIL -> 4/5
    sweep = setups.evaluate(d, signal("smc_sweep_mss"), None, CFG)
    assert sweep["score"] == 4 and not sweep["confirmed"] and any("mandatory" in m for m in sweep["missing"])
    trend = setups.evaluate(d, signal("vwap_trend"), None, CFG)  # mandatory: HTF bias + momentum
    assert trend["score"] == 4 and trend["confirmed"]
    strict = setups.evaluate(d, signal("vwap_trend"), None, setups.SetupConfig(min_confluence=5, costs=CFG.costs))
    assert not strict["confirmed"] and "4/5 confluences (need 5)" in strict["missing"]


def test_unknown_never_counts_as_pass() -> None:
    d = scenario()
    d["context"]["vwap"] = {"available": False}
    ev = setups.evaluate(d, signal("smc_ob_retest"), None, CFG)
    assert ev["factors"][4]["status"] == "UNKNOWN" and ev["score"] == 4


def test_same_market_event_is_not_double_counted() -> None:
    d = scenario()
    d["advanced"]["5m"]["fvgs"][0]["confirmed_at"] = iso(10)  # the FVG formed on the structure-break candle
    ev = setups.evaluate(d, signal(), None, CFG)
    assert ev["factors"][1]["status"] == "PASS" and ev["factors"][3]["status"] == "FAIL"


@pytest.mark.parametrize(
    ("mutate", "missing"),
    [
        (lambda d: d["news"].update(status="NEWS WINDOW ACTIVE"), "news"),
        (lambda d: d["news"].update(status="NEWS DATA STALE / UNAVAILABLE"), "calendar"),
        (lambda d: d["data"].update(label="STALE"), "market data STALE"),
        (lambda d: d["data"].update(contract=None), "contract"),
        (lambda d: d["levels"][1].update(price=21004.0), "net R:R"),  # opposing liquidity right in front
        (lambda d: d["context"].update(price=21030.0), "beyond the entry zone"),
    ],
)
def test_every_gate_blocks_confirmation(mutate: Any, missing: str) -> None:
    d = scenario()
    mutate(d)
    ev = setups.evaluate(d, signal(), None, CFG)
    assert not ev["confirmed"] and any(missing in m for m in ev["missing"]), ev["missing"]


def test_close_opposing_liquidity_is_tp1_not_a_distant_invented_target() -> None:
    d = scenario()
    d["levels"][1]["price"] = 21004.0
    lv = setups.evaluate(d, signal(), None, CFG)["levels"]
    assert lv["tp1"] == 21003.75 and lv["tp1"] != 21040


def test_stale_trigger_and_no_defensible_stop() -> None:
    assert not setups.evaluate(scenario(), signal(created_min_ago=40), None, CFG)["confirmed"]
    d = scenario()
    d["levels"][0]["state"] = "untouched"
    d["advanced"]["5m"]["fvgs"] = []
    d["advanced"]["5m"]["internal"]["pivots"] = []
    ev = setups.evaluate(d, signal(stop=20999.0), None, CFG)
    assert ev["levels"]["stop"] is None and not ev["confirmed"]


def test_evidence_labels_and_states() -> None:
    assert setups.evidence_label({"evidence": "Validated for paper-trading consideration"}) == "VALIDATED"
    assert setups.evidence_label({"evidence": "Promising but unvalidated"}) == "PROMISING BUT UNVALIDATED"
    assert setups.evidence_label({"evidence": "Negative"}) == "NEGATIVE EVIDENCE"
    assert setups.evidence_label(None) == "INSUFFICIENT EVIDENCE"
    d = scenario()
    d["levels"][0]["state"] = "untouched"
    assert setups.assess(d, [signal()], {}, CFG)["state"] == "DEVELOPING BUY"
    neutral = scenario()
    neutral["context"].update(bias_dir="neutral", short="neutral", htf="neutral", mid="neutral")
    neutral["advanced"]["5m"]["internal"]["events"] = []
    neutral["levels"][0]["state"] = "untouched"
    assert setups.assess(neutral, [], {}, CFG)["state"] == "WAIT"


# ----------------------------------------------------------------------------------- lifecycle


def mb(minute: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(NOW + timedelta(minutes=minute), o, h, low, c, 10)


BASE = {
    "symbol": "NQ",
    "direction": "long",
    "entry": 21000.0,
    "entry_low": 20998.0,
    "entry_high": 21002.0,
    "stop": 20994.0,
    "tp1": 21039.75,
    "confirmed_at": NOW,
    "expires_at": NOW + timedelta(minutes=30),
    "net_risk_usd": 135.0,
    "net_reward_usd": 785.0,
    "costs_usd": 15.0,
}


def test_lifecycle_tp1_stop_first_expired_missed_invalidated() -> None:
    win = signal_tracker.simulate(
        BASE,
        [
            mb(-1, 20990, 21050, 20980, 21040),  # before confirmation: ignored
            mb(1, 21004, 21005, 20999.5, 21003),
            mb(2, 21003, 21040, 21002, 21038),
        ],
    )
    assert (
        win["filled_at"] == NOW + timedelta(minutes=1)
        and win["outcome"] == "tp1"
        and win["outcome_r"] == round(785 / 135, 3)
    )
    both = signal_tracker.simulate(BASE, [mb(1, 21004, 21045, 20990, 21030)])  # fill, stop and target in one bar
    assert both["outcome"] == "stop" and both["outcome_usd"] == -135.0
    touch_only = signal_tracker.simulate(
        BASE, [mb(1, 21004, 21005, 20999, 21003), mb(2, 21003, 21039.75, 21001, 21030)]
    )
    assert touch_only["outcome"] is None  # TP1 needs a one-tick trade-through
    assert signal_tracker.simulate(BASE, [mb(31, 21004, 21005, 21003, 21004)])["status"] == "EXPIRED"
    assert signal_tracker.simulate(BASE, [mb(1, 21004, 21012, 21003, 21011)])["status"] == "MISSED"
    # A limit entry inside the zone is always filled before the stop can be crossed: no price-only
    # invalidation before entry (structure-based invalidation is tested below).
    gap = signal_tracker.simulate({**BASE, "entry": 20996.0}, [mb(1, 20999, 21000, 20993, 20993.5)])
    assert gap["filled_at"] is not None and gap["outcome"] == "stop"


def test_flat_at_1555_et() -> None:
    late = {
        **BASE,
        "confirmed_at": datetime(2026, 11, 18, 20, 40, tzinfo=UTC),
        "expires_at": datetime(2026, 11, 18, 21, 10, tzinfo=UTC),
    }
    bars = [
        Bar(datetime(2026, 11, 18, 20, 41, tzinfo=UTC), 21001, 21002, 20999, 21001, 1),
        Bar(datetime(2026, 11, 18, 20, 54, tzinfo=UTC), 21005, 21006, 21004, 21005, 1),
    ]
    out = signal_tracker.simulate(late, bars)
    assert out["outcome"] == "eod" and out["outcome_usd"] == 5 * 20 - 15


# ----------------------------------------------------------------------------- history / record


def recorded_doc(d: dict[str, Any] | None = None) -> dict[str, Any]:
    d = copy.deepcopy(d or scenario())
    d["signal"] = setups.assess(d, [signal()], {}, CFG)
    return d


async def test_record_once_never_rewritten_and_displayed() -> None:
    d = recorded_doc()
    async with get_sessionmaker()() as s:
        new = await signal_tracker.record(s, d, NOW + timedelta(seconds=20), 30)
        await s.commit()
        assert len(new) == 1 and new[0].qualified and new[0].status == "CONFIRMED"
        r = new[0]
        assert r.confirmed_at == NOW and r.detected_at == NOW + timedelta(seconds=20)  # knowable at the bar close
        assert r.contract == "NQZ6" and r.engine_version == setups.ENGINE_VERSION
        assert r.original["confirmed_at_et"].startswith("2026-11-18 10:30:00")
        # The same event evaluated again (even with different levels) does not create or change a record.
        d2 = recorded_doc()
        d2["signal"]["candidates"][0]["levels"]["stop"] = 1.0
        assert await signal_tracker.record(s, d2, NOW + timedelta(minutes=1), 30) == []
        await s.commit()
        rows = (await s.scalars(__import__("sqlalchemy").select(SetupSignal))).all()
        assert len(rows) == 1 and rows[0].stop == 20994.0
        shown = await signal_tracker.display(s, "NQ", NOW + timedelta(minutes=5))
        assert shown["display_state"] == "TECHNICAL BUY SETUP CONFIRMED" and shown["tp1"] == 21039.75
        assert (
            await signal_tracker.display(s, "NQ", NOW + timedelta(minutes=31)) is None
        )  # expired: not shown as active
        rows[0].status, rows[0].status_at = "EXPIRED", NOW + timedelta(minutes=30)
        await s.commit()
        ended = await signal_tracker.display(s, "NQ", NOW + timedelta(minutes=35))
        assert ended["display_state"] == "EXPIRED"


async def test_opposite_structure_shift_invalidates_before_entry() -> None:
    d = recorded_doc()
    async with get_sessionmaker()() as s:
        await signal_tracker.record(s, d, NOW, 30)
        later = copy.deepcopy(d)
        later["advanced"]["5m"]["internal"]["events"].append(
            {
                "time": (NOW + timedelta(minutes=5)).isoformat(),
                "direction": "bearish",
                "kind": "CHoCH",
                "level": 20990.0,
                "layer": "internal",
                "pivot_time": iso(0),
            }
        )
        assert await signal_tracker.invalidate_on_structure(s, later, NOW + timedelta(minutes=10)) == 1
        await s.commit()
        r = (await s.scalars(__import__("sqlalchemy").select(SetupSignal))).one()
        assert r.status == "INVALIDATED" and "CHoCH" in r.status_reason and r.stop == 20994.0


async def test_research_tier_recorded_separately_and_stale_data_not_recorded() -> None:
    d = scenario()
    d["levels"][0]["state"] = "untouched"  # 4/5 but the sweep is mandatory -> research observation only
    async with get_sessionmaker()() as s:
        new = await signal_tracker.record(s, recorded_doc(d), NOW, 30)
        assert len(new) == 1 and not new[0].qualified and new[0].status == "OBSERVED" and new[0].score == 4
        stale = recorded_doc()
        stale["data"]["label"] = "STALE"
        assert await signal_tracker.record(s, stale, NOW, 30) == []


def test_threshold_comparison_is_honest_about_small_samples() -> None:
    items = []
    for i in range(12):
        items.append(
            {
                "outcome": "tp1" if i % 3 else "stop",
                "outcome_usd": 100.0 if i % 3 else -50.0,
                "outcome_r": 2.0 if i % 3 else -1.0,
                "outcome_at": NOW + timedelta(days=i),
                "confirmed_at": NOW + timedelta(days=i),
                "strategy_id": "orb",
                "symbol": "ES",
                "score": 3 + i % 3,
                "costs_usd": 5.0,
            }
        )
    cmp = signal_tracker.compare_thresholds(items, {3: 4, 4: 4, 5: 4})
    tiers = cmp["tiers"]
    assert tiers["3/5 loose (research only)"]["resolved"] == 12 and tiers["5/5 strict"]["resolved"] == 4
    assert tiers["4/5 balanced (live threshold)"]["signals"] == 8
    # Only 12 resolved outcomes: never declare a winner, however different the numbers look.
    assert cmp["verdict"].startswith("INCONCLUSIVE") and "not a probability" in cmp["caveat"]


def test_analyzer_ignores_signals_created_after_now() -> None:
    from app.services import analyzer

    later = signal(created_min_ago=-5)  # created 5 minutes in the future
    doc = analyzer.build("NQ", [], [], NOW, {}, "simulated", None, [], [later], {})
    assert doc["signal"]["state"] == "WAIT"


async def test_confluence_study_excludes_the_holdout() -> None:
    from app.adapters.marketdata.simulated import SimulatedProvider
    from app.research import confluence_study as cs

    c = await SimulatedProvider(clock=lambda: NOW).fetch_candles("ES", NOW - timedelta(days=6), NOW)
    bars = [Bar(x.ts, float(x.open), float(x.high), float(x.low), float(x.close), int(x.volume)) for x in c]
    sessions = cs.rth_sessions(bars)
    r = cs.run("ES", bars, [], holdout_sessions=2, strategies=["vwap_trend", "orb"], cfg=CFG)
    assert r["period"]["last"] == sessions[-3] and r["period"]["holdout_starts"] == sessions[-2]
    assert set(r["comparison"]["tiers"]) == {"3/5 loose (research only)", "4/5 balanced (live threshold)", "5/5 strict"}
    assert "error" in cs.run("ES", bars, [], holdout_sessions=50)


async def test_signal_api_is_read_only(admin_client: httpx.AsyncClient) -> None:
    from tests.conftest import CSRF

    assert (await admin_client.get("/api/signals")).json() == {"signals": []}
    st = (await admin_client.get("/api/signals/stats")).json()
    assert st["recorded"] == 0 and st["comparison"]["verdict"].startswith("INCONCLUSIVE")
    assert (await admin_client.post("/api/signals", headers=CSRF)).status_code == 405
