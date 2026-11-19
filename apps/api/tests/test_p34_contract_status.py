"""Regression: the Databento feed stores each contract as {contract, instrument_id, rule, since}.

The analyzer used to pass that dict straight through as ``data.contract``. The dashboard rendered it as a
React child (the whole page crashed with real data) and the immutable setup record tried to write a dict
into the varchar ``setup_signals.contract`` column, so the first confirmed live setup could never be stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.db import get_sessionmaker
from app.research.bars import Bar
from app.services import analyzer, setups, signal_tracker
from tests.test_p29_setups import CFG, NOW, scenario, signal

FEED = {
    "state": "streaming",
    "quality": "live",
    "contracts": {
        "NQ": {"contract": "NQZ6", "instrument_id": 261401, "rule": "v", "since": "2026-11-19T02:30:51+00:00"},
    },
}


def _bars(now: datetime) -> list[Bar]:
    return [Bar(now - timedelta(minutes=2), 27490, 27495, 27488, 27492, 50)]


def test_data_status_exposes_contract_as_string_and_instrument_id_separately() -> None:
    now = datetime(2026, 11, 19, 4, 31, tzinfo=UTC)
    d = analyzer._data_status("NQ", _bars(now), FEED, "databento", now)
    assert d["contract"] == "NQZ6" and isinstance(d["contract"], str)
    assert d["instrument_id"] == 261401


def test_data_status_tolerates_missing_or_legacy_string_mapping() -> None:
    now = datetime(2026, 11, 19, 4, 31, tzinfo=UTC)
    assert analyzer._data_status("MNQ", _bars(now), FEED, "databento", now)["contract"] is None
    legacy = {**FEED, "contracts": {"NQ": "NQZ6"}}
    d = analyzer._data_status("NQ", _bars(now), legacy, "databento", now)
    assert d["contract"] == "NQZ6" and d["instrument_id"] is None


async def test_setup_with_live_feed_contract_mapping_is_recorded() -> None:
    d = scenario()
    d["data"] = {**d["data"], **analyzer._data_status("NQ", _bars(NOW), FEED, "databento", NOW)}
    d["signal"] = setups.assess(d, [signal()], {}, CFG)
    async with get_sessionmaker()() as s:
        new = await signal_tracker.record(s, d, NOW + timedelta(seconds=20), 30)
        await s.commit()
    assert len(new) == 1 and new[0].contract == "NQZ6"


async def test_negative_evidence_never_hides_detection_or_forward_record() -> None:
    """A 4/5 technical setup is detected, levelled and recorded even when its strategy has negative evidence;
    the evidence is carried as a label (the readiness gate is separate: test_p22 evidence-gate test)."""
    d = scenario()
    negative = {signal()["strategy_id"]: {"available": True, "evidence": "Negative evidence", "unmet": ["expectancy"]}}
    d["signal"] = setups.assess(d, [signal()], negative, CFG)
    assert d["signal"]["state"] == "TECHNICAL BUY SETUP CONFIRMED"
    best = d["signal"]["best"]
    assert best["evidence"] == "NEGATIVE EVIDENCE"
    assert best["levels"]["stop"] is not None and best["levels"]["tp1"] is not None
    async with get_sessionmaker()() as s:
        new = await signal_tracker.record(s, d, NOW + timedelta(seconds=20), 30)
        await s.commit()
    assert len(new) == 1 and new[0].qualified and new[0].evidence == "NEGATIVE EVIDENCE"
    c = analyzer.card({**d, "name": "E-mini Nasdaq-100", "session": {}, "bias": {}})
    assert c["evidence"] == "NEGATIVE EVIDENCE"
