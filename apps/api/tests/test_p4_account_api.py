from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.signal import AlertRow
from app.models.user import User
from app.research.scanner import ScannerService
from app.services.account import eod_timestamp, parse_balance_csv
from tests.conftest import CSRF

OFFICIAL = "https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/"
VERIFY = {
    "source_url": OFFICIAL,
    "applies_to": "50K EOD Evaluation (Tradovate)",
    "version": "checked 2026-11-17",
    "confirm": True,
}


async def post(c: httpx.AsyncClient, path: str, body: object | None = None) -> httpx.Response:
    return await c.post(path, json=body, headers=CSRF)


# --- rule profile ----------------------------------------------------------------------------


async def test_rule_profile_seeds_unverified(admin_client: httpx.AsyncClient) -> None:
    r = (await admin_client.get("/api/rules")).json()
    assert r["profile"] == "apex-50k-eod-evaluation" and r["verified"] is False
    by = {x["key"]: x for x in r["rules"]}
    assert len(r["rules"]) == 16 and len(r["unverified"]) == 15  # 15 required + 1 optional
    assert [
        by[k]["value"]
        for k in ("starting_balance", "profit_target", "max_drawdown", "daily_loss_limit", "max_contracts")
    ] == ["50000", "3000", "2000", "1000", "6"]
    assert all(not x["verified"] and x["source_url"] is None for x in r["rules"])
    assert all(x["suggested_source_url"].startswith("https://apextraderfunding.com/") for x in r["rules"])
    assert "No trade will be labelled compliant" in r["warning"]


async def test_verify_requires_official_https_source_and_confirmation(
    admin_client: httpx.AsyncClient,
) -> None:
    for bad in (
        "http://apextraderfunding.com/x/yy",
        "https://apextraderfunding.com.evil.io/rules",
        "https://reddit.com/r/apex/rules",
    ):
        r = await post(admin_client, "/api/rules/max_drawdown/verify", {**VERIFY, "source_url": bad})
        assert r.status_code == 422, bad
    no_confirm = {k: v for k, v in VERIFY.items() if k != "confirm"}
    assert (await post(admin_client, "/api/rules/max_drawdown/verify", no_confirm)).status_code == 422
    ok = await post(admin_client, "/api/rules/max_drawdown/verify", VERIFY)
    assert ok.status_code == 200
    rule = next(x for x in ok.json()["rules"] if x["key"] == "max_drawdown")
    assert rule["verified"] and rule["source_url"] == OFFICIAL and rule["verified_at"] and rule["version"]
    sub = await post(
        admin_client,
        "/api/rules/max_drawdown/verify",
        {**VERIFY, "source_url": "https://support.apextraderfunding.com/hc/en-us/articles/1"},
    )
    assert sub.status_code == 200
    assert (await post(admin_client, "/api/rules/nope/verify", VERIFY)).status_code == 404


async def test_changing_a_value_clears_its_verification_and_is_audited(
    admin_client: httpx.AsyncClient,
) -> None:
    await post(admin_client, "/api/rules/daily_loss_limit/verify", VERIFY)
    r = await admin_client.put("/api/rules/daily_loss_limit", json={"value": "$1,250"}, headers=CSRF)
    rule = next(x for x in r.json()["rules"] if x["key"] == "daily_loss_limit")
    assert rule["value"] == "1250" and rule["verified"] is False and rule["source_url"] is None
    # Re-saving the same value keeps verification.
    await post(admin_client, "/api/rules/daily_loss_limit/verify", VERIFY)
    r = await admin_client.put("/api/rules/daily_loss_limit", json={"value": "1250"}, headers=CSRF)
    assert next(x for x in r.json()["rules"] if x["key"] == "daily_loss_limit")["verified"] is True
    for bad in ("-5", "abc", "0", ""):
        assert (
            await admin_client.put("/api/rules/daily_loss_limit", json={"value": bad}, headers=CSRF)
        ).status_code == 422, bad
    assert (
        await admin_client.put("/api/rules/max_contracts", json={"value": "6.5"}, headers=CSRF)
    ).status_code == 422
    # Optional rule may be blank.
    assert (
        await admin_client.put("/api/rules/drawdown_stop_level", json={"value": ""}, headers=CSRF)
    ).status_code == 200
    async with get_sessionmaker()() as s:
        actions = (await s.scalars(select(AuditLog.action))).all()
    assert actions.count("rules.verify") == 2 and "rules.update" in actions


async def test_profile_becomes_verified_only_when_every_required_rule_is(
    admin_client: httpx.AsyncClient,
) -> None:
    rules = (await admin_client.get("/api/rules")).json()["rules"]
    required = [r["key"] for r in rules if r["required"]]
    for key in required[:-1]:
        await post(admin_client, f"/api/rules/{key}/verify", VERIFY)
    assert (await admin_client.get("/api/rules")).json()["verified"] is False
    await post(admin_client, f"/api/rules/{required[-1]}/verify", VERIFY)
    body = (await admin_client.get("/api/rules")).json()
    assert body["verified"] is True and body["warning"] is None
    status = (await admin_client.get("/api/system/status")).json()
    comp = next(c for c in status["components"] if c["name"] == "apex_rule_profile")
    assert comp["state"] == "ok"
    await post(admin_client, f"/api/rules/{required[0]}/unverify")
    assert (await admin_client.get("/api/rules")).json()["verified"] is False


async def test_viewers_cannot_change_rules_or_delete_snapshots(admin_client: httpx.AsyncClient) -> None:
    async with get_sessionmaker()() as s:
        s.add(User(email="viewer@example.com", password_hash=hash_password("Viewer-Pass-42!"), role="viewer"))
        await s.commit()
    snap = await post(admin_client, "/api/account/snapshots", {"balance": "50000"})
    sid = snap.json()["id"]
    admin_client.cookies.clear()
    await admin_client.post(
        "/api/auth/login", json={"email": "viewer@example.com", "password": "Viewer-Pass-42!"}
    )
    assert (await admin_client.get("/api/rules")).status_code == 200
    assert (
        await admin_client.put("/api/rules/max_drawdown", json={"value": "1"}, headers=CSRF)
    ).status_code == 403
    assert (await post(admin_client, "/api/rules/max_drawdown/verify", VERIFY)).status_code == 403
    assert (await admin_client.delete(f"/api/account/snapshots/{sid}", headers=CSRF)).status_code == 403


async def test_account_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    for path in ("/api/rules", "/api/account/state", "/api/account/snapshots"):
        assert (await client.get(path)).status_code == 401
    assert (await client.post("/api/risk/check", json={})).status_code == 401


# --- snapshots and state ---------------------------------------------------------------------


async def test_unknown_account_until_a_snapshot_exists(admin_client: httpx.AsyncClient) -> None:
    body = (await admin_client.get("/api/account/state")).json()
    st = body["state"]
    assert st["known"] is False and st["risk_budget"] is None and st["equity"] is None
    assert any("UNKNOWN" in w for w in st["warnings"]) and "Advisory only" in body["disclaimer"]


async def test_manual_snapshot_drives_account_state(admin_client: httpx.AsyncClient) -> None:
    yesterday = date.today() - timedelta(days=1)
    r = await post(
        admin_client,
        "/api/account/snapshots",
        {
            "balance": "51000.00",
            "is_eod": True,
            "trading_date": yesterday.isoformat(),
            "as_of": eod_timestamp(yesterday).isoformat(),
        },
    )
    assert r.status_code == 201 and r.json()["source"] == "manual"
    r = await post(
        admin_client,
        "/api/account/snapshots",
        {
            "balance": "50700",
            "unrealized_pnl": "-150",
            "positions": [
                {"symbol": "es", "quantity": 2, "avg_price": "5000.25", "stop": "4995"},
                {"symbol": "MNQ", "quantity": -3, "avg_price": "18000"},
            ],
        },
    )
    assert r.status_code == 201 and r.json()["positions"][0]["symbol"] == "ES"
    st = (await admin_client.get("/api/account/state")).json()["state"]
    assert st["known"] and st["balance"] == 50700 and st["equity"] == 50550
    assert st["drawdown_threshold"] == 49000 and st["drawdown_room"] == 1550
    assert st["contracts_in_use"] == pytest.approx(2.3)
    assert any("no stop recorded" in w for w in st["warnings"])
    snaps = (await admin_client.get("/api/account/snapshots")).json()
    assert len(snaps) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"balance": "-1"},
        {"balance": "0"},
        {"balance": "abc"},
        {"balance": "50000", "as_of": "2026-01-01T00:00:00"},  # naive
        {"balance": "50000", "as_of": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        {"balance": "50000", "is_eod": True, "unrealized_pnl": "25"},
        {"balance": "50000", "positions": [{"symbol": "ZZZ", "quantity": 1, "avg_price": "1"}]},
        {"balance": "50000", "positions": [{"symbol": "ES", "quantity": 0, "avg_price": "1"}]},
        {"balance": "50000", "extra": 1},
        {"balance": "50000.123"},
    ],
)
async def test_snapshot_validation(admin_client: httpx.AsyncClient, body: dict) -> None:
    assert (await post(admin_client, "/api/account/snapshots", body)).status_code == 422


async def test_delete_snapshot(admin_client: httpx.AsyncClient) -> None:
    sid = (await post(admin_client, "/api/account/snapshots", {"balance": "50000"})).json()["id"]
    assert (await admin_client.delete(f"/api/account/snapshots/{sid}", headers=CSRF)).status_code == 204
    assert (await admin_client.delete(f"/api/account/snapshots/{sid}", headers=CSRF)).status_code == 404
    assert (await admin_client.get("/api/account/state")).json()["state"]["known"] is False


# --- CSV import ------------------------------------------------------------------------------


def test_csv_parser_accepts_common_layouts() -> None:
    a = parse_balance_csv('Date,End of Day Balance\n2026-11-09,"$50,400.00"\n11/10/2026,50900\n')
    assert a.errors == [] and a.imported == [
        (date(2026, 11, 9), Decimal("50400.00")),
        (date(2026, 11, 10), Decimal("50900")),
    ]
    b = parse_balance_csv("﻿Trade Date,Account,Balance\n2026-11-10 16:59:00,APEX1,51000.5\n\n")
    assert b.errors == [] and b.imported == [(date(2026, 11, 10), Decimal("51000.5"))]
    assert b.date_column == "Trade Date" and b.balance_column == "Balance"
    dup = parse_balance_csv("date,balance\n2026-11-10,1\n2026-11-10,50500\n")
    assert dup.imported == [(date(2026, 11, 10), Decimal("50500"))]  # later row wins


def test_csv_parser_reports_problems_instead_of_guessing() -> None:
    assert "Could not find" in parse_balance_csv("when,amount\n2026-11-10,1\n").errors[0]
    assert parse_balance_csv("").errors == ["File is empty."]
    bad = parse_balance_csv("date,balance\n2026-13-45,50000\n2026-11-10,abc\n2026-11-11,(25)\n2026-11-12\n")
    assert len(bad.errors) == 4 and bad.imported == []
    assert "too large" in parse_balance_csv("date,balance\n" + "x" * 300_000).errors[0]


async def test_csv_import_dry_run_then_commit(admin_client: httpx.AsyncClient) -> None:
    csv_text = "Date,Balance\n2026-11-09,50400\n2026-11-10,51200\n"
    dry = (await post(admin_client, "/api/account/import-csv", {"csv": csv_text, "dry_run": True})).json()
    assert dry["imported"] == 0 and len(dry["rows"]) == 2
    assert (await admin_client.get("/api/account/snapshots")).json() == []
    done = (await post(admin_client, "/api/account/import-csv", {"csv": csv_text})).json()
    assert done["imported"] == 2
    snaps = (await admin_client.get("/api/account/snapshots")).json()
    assert {s["source"] for s in snaps} == {"csv"} and all(s["is_eod"] for s in snaps)
    st = (await admin_client.get("/api/account/state")).json()["state"]
    assert st["highest_eod_balance"] == 51200 and st["drawdown_threshold"] == 49200
    # Any bad row blocks the whole import.
    mixed = (await post(admin_client, "/api/account/import-csv", {"csv": csv_text + "garbage,row\n"})).json()
    assert mixed["imported"] == 0 and mixed["errors"]


# --- risk check ------------------------------------------------------------------------------


async def test_risk_check_endpoint(admin_client: httpx.AsyncClient) -> None:
    trade = {
        "symbol": "ES",
        "direction": "long",
        "entry": "5000",
        "stop": "4996",
        "target": "5008",
        "quantity": 2,
    }
    unknown = (await post(admin_client, "/api/risk/check", trade)).json()
    assert unknown["check"]["verdict"] == "cannot_confirm" and unknown["check"]["max_size"] is None
    await post(admin_client, "/api/account/snapshots", {"balance": "50000"})
    r = (await post(admin_client, "/api/risk/check", trade)).json()
    c = r["check"]
    assert c["dollar_risk"] == 400 and c["loss_after_costs"] == 460 and c["max_size"] == 4
    assert c["reward_risk"] == 2 and c["budget_used_pct"] == 46 and c["violations"] == []
    assert c["verdict"] == "no_violation_found" and c["may_be_called_compliant"] is False
    assert r["instrument"]["point_value"] == 50 and r["cost_assumptions"]["commission_per_side"] == 2.5
    bad = await post(admin_client, "/api/risk/check", {**trade, "stop": "5001"})
    assert bad.status_code == 422 and "stop must be below entry" in bad.text
    assert (await post(admin_client, "/api/risk/check", {**trade, "symbol": "ZZZ"})).status_code == 404
    assert (await post(admin_client, "/api/risk/check", {**trade, "entry": "5000.1"})).status_code == 422
    assert (await post(admin_client, "/api/risk/check", {**trade, "quantity": 0})).status_code == 422


# --- alerts ----------------------------------------------------------------------------------


async def test_scanner_raises_account_alerts(admin_client: httpx.AsyncClient) -> None:
    from app.core.config import get_settings

    now = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)  # market open
    async with get_sessionmaker()() as s:
        from app.models.account import AccountSnapshot

        s.add(
            AccountSnapshot(
                as_of=now - timedelta(hours=2),
                source="manual",
                balance=Decimal("49200"),
                unrealized_pnl=Decimal("0"),
                is_eod=False,
                trading_date=date(2026, 11, 11),
                positions=[],
            )
        )
        await s.commit()
    scanner = ScannerService(get_settings(), get_sessionmaker(), get_redis(), clock=lambda: now)
    await scanner.scan_once()
    await scanner.scan_once()  # deduplicated
    async with get_sessionmaker()() as s:
        alerts = (await s.scalars(select(AlertRow))).all()
    kinds = sorted(a.kind for a in alerts)
    assert kinds == ["account_stale", "risk_threshold"]
    risk = next(a for a in alerts if a.kind == "risk_threshold")
    assert risk.severity == "warning" and "$200" in risk.body  # 1000 - 800 lost today
