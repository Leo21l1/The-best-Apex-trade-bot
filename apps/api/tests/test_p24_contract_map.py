"""Actual contract maps: two-stage Databento symbology, validation, coverage, CLI failure handling."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.adapters.marketdata.base import ProviderRequestError
from app.adapters.marketdata.databento import DatabentoProvider
from app.research import rollover
from app.research.contract_map import ContractMapError, build_document, require_coverage, validate_document, verify
from app.research.study import run_full_study
from app.risk.engine import RuleSet
from tests.test_p22_research_engine import _series

RULES = RuleSet("test", Decimal(50000), Decimal(3000), Decimal(2000), Decimal(1000), Decimal(6))
STAGE1 = [
    {"d0": "2026-06-01", "d1": "2026-06-12", "s": "111"},
    {"d0": "2026-06-12", "d1": "2026-09-11", "s": "222"},
    {"d0": "2026-09-11", "d1": "2026-10-01", "s": "333"},
]
STAGE2 = {
    "111": [{"d0": "2026-01-01", "d1": "2026-12-31", "s": "ESM6"}],
    "222": [{"d0": "2026-01-01", "d1": "2026-12-31", "s": "ESU6"}],
    "333": [{"d0": "2026-01-01", "d1": "2026-12-31", "s": "ESZ6"}],
}


def provider(stage2: dict[str, Any] | None = None, fail: int | None = None) -> tuple[DatabentoProvider, list[dict]]:
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        form = {k: httpx.QueryParams(f"x={v}")["x"] for k, v in form.items()}
        seen.append(form)
        if (form["stype_in"], form["stype_out"]) == ("continuous", "raw_symbol"):
            return httpx.Response(
                422,
                json={
                    "detail": {
                        "case": "symbology_invalid_request",
                        "message": "dataset=GLBX.MDP3, stype_in=continuous, stype_out=raw_symbol",
                    }
                },
            )
        if fail == len(seen):
            return httpx.Response(422, json={"detail": {"case": "symbology_invalid_request", "message": "bad"}})
        if form["stype_in"] == "continuous":
            return httpx.Response(200, json={"result": {"ES.v.0": STAGE1}, "partial": [], "not_found": []})
        res = {i: (stage2 if stage2 is not None else STAGE2).get(i, []) for i in form["symbols"].split(",")}
        return httpx.Response(200, json={"result": res, "partial": [], "not_found": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://hist.databento.test")
    return DatabentoProvider("k" * 20, client=client, min_interval_s=0, max_retries=0), seen


async def test_two_stage_resolution_across_quarterly_rolls_never_requests_the_unsupported_pair() -> None:
    p, seen = provider()
    r = await p.resolve_contracts("ES", date(2026, 6, 1), date(2026, 10, 1))
    assert [(x["stype_in"], x["stype_out"]) for x in seen] == [
        ("continuous", "instrument_id"),
        ("instrument_id", "raw_symbol"),
    ]
    assert seen[1]["symbols"] == "111,222,333"
    m = r["map"]
    assert m["2026-06-11"] == "ESM6" and m["2026-06-12"] == "ESU6" and m["2026-09-10"] == "ESU6"
    assert m["2026-09-11"] == "ESZ6" and "2026-10-01" not in m and r["unresolved"] == []
    assert r["instrument_ids"]["2026-09-11"] == "333"
    sw = rollover.compare_to_calendar("ES", m)
    assert [(c["switch"], c["calendar_roll"], c["days_from_calendar"]) for c in sw] == [
        ("2026-06-12", "2026-06-11", 1),
        ("2026-09-11", "2026-09-10", 1),
    ]
    assert all(c["inside_calendar_window"] for c in sw)


async def test_stage_failures_are_actionable_and_unresolved_dates_are_never_filled() -> None:
    p, _ = provider(fail=1)
    with pytest.raises(ProviderRequestError, match=r"stage 1 \(continuous -> instrument_id\) failed: HTTP 422"):
        await p.resolve_contracts("ES", date(2026, 6, 1), date(2026, 10, 1))
    p, _ = provider(
        stage2={"111": STAGE2["111"], "222": STAGE2["222"], "333": [{"d0": "2026-09-11", "d1": "2026-09-20", "s": ""}]}
    )
    r = await p.resolve_contracts("ES", date(2026, 6, 1), date(2026, 10, 1))
    assert "2026-09-11" in r["unresolved"] and "2026-09-11" not in r["map"]  # empty symbol skipped, not inferred


def test_document_schema_validation() -> None:
    doc = build_document(
        "GLBX.MDP3",
        "now",
        {
            "ES": {
                "continuous": "ES.v.0",
                "start": "2026-06-01",
                "end": "2026-10-01",
                "map": {"2026-06-11": "ESM6", "2026-06-12": "ESU6"},
            }
        },
    )
    assert doc["symbols"]["ES"]["switches"] == [{"date": "2026-06-12", "from": "ESM6", "to": "ESU6"}]
    assert validate_document(json.loads(json.dumps(doc)), ["ES"]) == {
        "ES": {"2026-06-11": "ESM6", "2026-06-12": "ESU6"}
    }
    for broken, msg in (
        ({"ES": {"2026-06-01": "ESZ6"}}, "not an APEX contract map"),
        ({**doc, "version": 9}, "unsupported contract map version"),
        (doc, "no mapping for NQ"),
    ):
        with pytest.raises(ContractMapError, match=msg):
            validate_document(broken, ["ES", "NQ"] if msg.startswith("no mapping") else ["ES"])
    bad = json.loads(json.dumps(doc))
    bad["symbols"]["ES"]["map"]["2026-06-13"] = "NQU6"
    with pytest.raises(ContractMapError, match="is not a ES contract"):
        validate_document(bad, ["ES"])


def test_coverage_and_verification_against_stored_bars() -> None:
    b = _series(datetime(2026, 9, 3, 22, 0, tzinfo=UTC), datetime(2026, 9, 25, 21, 0, tzinfo=UTC), date(2026, 9, 11))
    days = sorted({rollover.trading_day(x.ts).isoformat() for x in b})
    m = {d: ("ESU6" if d < "2026-09-11" else "ESZ6") for d in days}
    with pytest.raises(ContractMapError, match="no actual contract mapping"):
        require_coverage("ES", {k: v for k, v in m.items() if k != "2026-09-17"}, days)
    v = verify("ES", m, b, days)
    assert v["ok"] and v["contracts"] == ["ESU6", "ESZ6"] and not v["stored_trade_dates_unmapped"]
    assert v["discontinuities"] and all(x["at_actual_switch"] for x in v["discontinuities"])
    flip = {**m, "2026-09-18": "ESU6"}
    assert any("returns to ESU6" in p for p in verify("ES", flip, b, days)["problems"])


def test_study_uses_actual_contracts_and_refuses_incomplete_maps() -> None:
    b = _series(datetime(2026, 9, 3, 22, 0, tzinfo=UTC), datetime(2026, 9, 25, 21, 0, tzinfo=UTC), date(2026, 9, 11))
    days = sorted({rollover.trading_day(x.ts).isoformat() for x in b})
    m = {d: ("ESU6" if d < "2026-09-11" else "ESZ6") for d in days}
    res = run_full_study("ES", b, "historical", RULES, 2.5, 1.0, strategies=["vwap_trend"], contract_map=m)
    assert res["rollover"]["contract_source"].startswith("Databento")
    assert [s["contract"] for s in res["rollover"]["segments"]] == ["ESU6", "ESZ6"]
    assert "actual switch on 2026-09-11" in res["rollover"]["excluded_sessions"]["2026-09-11"]
    with pytest.raises(ContractMapError):
        run_full_study(
            "ES",
            b,
            "historical",
            RULES,
            2.5,
            1.0,
            strategies=["vwap_trend"],
            contract_map={k: v for k, v in m.items() if k != "2026-09-04"},
        )


async def test_cli_writes_no_file_on_failure_and_rejects_invalid_maps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.marketdata.factory as factory
    from app.cli import contract_map_cmd, research_study_cmd
    from app.core.config import get_settings

    get_settings().market_data_provider = "databento"
    p, _ = provider(fail=1)
    monkeypatch.setattr(factory, "build_provider", lambda _s: p)
    out = tmp_path / "map.json"
    assert await contract_map_cmd(["ES"], 30, str(out)) == 3
    text = capsys.readouterr().out
    assert "FAILED" in text and "stage 1" in text and "Nothing was downloaded" in text and not out.exists()
    out.write_text(json.dumps({"ES": {"2026-06-01": "ESZ6"}}))
    assert await research_study_cmd(["ES"], 30, ["vwap_trend"], 1, str(tmp_path), str(out)) == 2
    assert "Invalid contract map" in capsys.readouterr().out
    assert await research_study_cmd(["ES"], 30, ["vwap_trend"], 1, str(tmp_path), str(tmp_path / "nope.json")) == 2
