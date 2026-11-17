"""Manual broker statement import: layouts, FIFO pairing, duplicates, plan matching, reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.models.account import AccountSnapshot
from app.models.research import JournalTrade, TradePlan
from app.services.broker_import import detect_layout, parse_cash, parse_fills, parse_positions
from tests.conftest import CSRF

FEE = {"standard": 2.5, "micro": 0.6}

# Shaped like a Tradovate Orders report (one row per order; cancelled orders included).
ORDERS = """orderId,Account,Order ID,B/S,Contract,Product,avgPrice,filledQty,Fill Time,Status,Timestamp,\
Date,Quantity,Type,Filled Qty,Avg Fill Price
1,APEX-1,1001, Buy,MESZ6,MES,5000,2,11/11/2026 10:00:00, Filled,11/11/2026 10:00:00,11/11/26,2,Market,2,5000.00
2,APEX-1,1002, Sell,MESZ6,MES,5004,1,11/11/2026 10:05:00, Filled,11/11/2026 10:05:00,11/11/26,1,Limit,1,5004.00
3,APEX-1,1003, Sell,MESZ6,MES,,0,, Canceled,11/11/2026 10:06:00,11/11/26,1,Stop,0,
4,APEX-1,1004, Sell,MESZ6,MES,4998,1,11/11/2026 10:10:00, Filled,11/11/2026 10:10:00,11/11/26,1,Stop,1,4998.00
5,APEX-1,1005, Sell,MESZ6,MES,5010,1,11/11/2026 10:20:00, Filled,11/11/2026 10:20:00,11/11/26,1,Market,1,5010.00
6,APEX-1,1006, Buy,MESZ6,MES,5006,1,11/11/2026 10:30:00, Filled,11/11/2026 10:30:00,11/11/26,1,Market,1,5006.00
"""

# The same three round trips as a Tradovate Performance report.
PERFORMANCE = """symbol,_priceFormat,_priceFormatType,_tickSize,buyFillId,sellFillId,qty,buyPrice,sellPrice,pnl,\
boughtTimestamp,soldTimestamp,duration
MESZ6,-2,0,0.25,1001,1002,1,5000.00,5004.00,$20.00,11/11/2026 10:00:00,11/11/2026 10:05:00,5min
MESZ6,-2,0,0.25,1001,1004,1,5000.00,4998.00,$(10.00),11/11/2026 10:00:00,11/11/2026 10:10:00,10min
MESZ6,-2,0,0.25,1006,1005,1,5006.00,5010.00,$20.00,11/11/2026 10:30:00,11/11/2026 10:20:00,10min
"""

CASH = """Date,Cash Change Type,Delta,Total Amount
11/11/2026,Trade Paired,20.00,50020.00
11/11/2026,Commission,-1.20,50018.80
11/11/2026,Trade Paired,-10.00,50008.80
11/11/2026,Commission,-1.20,50007.60
11/11/2026,Trade Paired,20.00,50027.60
11/11/2026,Commission,-1.20,50026.40
"""

POSITIONS = """Contract,Net Pos,Net Price,Open P&L
ESZ6,1,5000.00,$25.00
NQZ6,0,0,$0.00
"""


def test_layout_detection() -> None:
    first = lambda s: s.splitlines()[0].split(",")  # noqa: E731
    assert detect_layout(first(ORDERS)) == "fills"
    assert detect_layout(first(PERFORMANCE)) == "performance"
    assert detect_layout(first(CASH)) == "cash"
    assert detect_layout(first(POSITIONS)) == "positions"
    assert detect_layout(["Date", "End of Day Balance"]) == "balances"
    assert detect_layout(["foo", "bar"]) is None


def test_fifo_pairing_of_fills_including_a_reversal() -> None:
    r = parse_fills(ORDERS, "America/New_York", FEE)
    assert r.errors == [] and r.fills == 5 and r.skipped_unfilled == 1
    got = [(t.direction, t.quantity, t.entry_price, t.exit_price, t.gross_pnl, t.fees) for t in r.trades]
    # MES $5/point; estimated $0.60 per side.
    assert got == [
        ("long", 1, 5000.0, 5004.0, 20.0, 1.2),
        ("long", 1, 5000.0, 4998.0, -10.0, 1.2),
        ("short", 1, 5010.0, 5006.0, 20.0, 1.2),
    ]
    assert r.trades[0].entry_time == datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # 10:00 ET
    assert r.fees_estimated and r.open_lots == []
    assert len({t.source_key for t in r.trades}) == 3


def test_open_lots_and_commission_column() -> None:
    text = (
        "Fill ID,B/S,Contract,Qty,Price,Timestamp,Commission\n"
        "A,Buy,ESZ6,3,5000,2026-11-11 10:00:00,7.50\nB,Sell,ESZ6,1,5002,2026-11-11 10:01:00,2.50\n"
    )
    r = parse_fills(text, "America/New_York", FEE)
    assert len(r.trades) == 1 and r.trades[0].fees == 5.0 and r.trades[0].gross_pnl == 100.0  # 2pt x $50
    assert r.open_lots[0]["quantity"] == 2 and not r.fees_estimated


def test_cash_history_and_positions() -> None:
    c = parse_cash(CASH, "America/New_York")
    assert c.errors == [] and len(c.days) == 1
    d = c.days[0]
    assert d["date"] == "2026-11-11" and d["realized"] == 30.0 and d["commission"] == -3.6
    assert d["end_balance"] == 50026.40 and d["net"] == 26.4
    p = parse_positions(POSITIONS)
    assert p.positions == [{"symbol": "ES", "contract": "ESZ6", "quantity": 1, "avg_price": 5000.0, "open_pnl": 25.0}]
    assert p.open_pnl == 25.0


def files(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"name": n, "csv": c} for n, c in pairs]


async def test_policy_endpoint_reports_api_sync_not_permitted(admin_client: httpx.AsyncClient) -> None:
    p = (await admin_client.get("/api/statements/policy")).json()
    assert p["api_sync_permitted"] is False and p["api_sync_implemented"] is False
    assert any("evaluation accounts are not eligible" in f["finding"] for f in p["findings"])


async def test_import_dry_run_then_apply_with_dedupe_matching_and_reconciliation(
    admin_client: httpx.AsyncClient,
) -> None:
    async with get_sessionmaker()() as s:
        s.add(
            AccountSnapshot(
                as_of=datetime(2026, 11, 10, 22, 0, tzinfo=UTC),
                source="manual",
                balance=50000,
                unrealized_pnl=0,
                is_eod=True,
                trading_date=datetime(2026, 11, 10).date(),
                positions=[],
            )
        )
        s.add(
            TradePlan(
                symbol="MES",
                direction="long",
                stop=4996,
                target=5008,
                quantity=2,
                status="executed_manual",
                execution="manual",
                created_at=datetime(2026, 11, 11, 14, 50, tzinfo=UTC),
                thesis="ORB long",
            )
        )
        await s.commit()
    body = {
        "files": files(
            ("orders.csv", ORDERS), ("performance.csv", PERFORMANCE), ("cash.csv", CASH), ("positions.csv", POSITIONS)
        ),
        "timezone": "America/New_York",
        "dry_run": True,
    }
    dry = (await admin_client.post("/api/statements/import", json=body, headers=CSRF)).json()
    assert [f["layout"] for f in dry["files"]] == ["fills", "performance", "cash", "positions"]
    assert dry["trades"] == {"parsed": 6, "new": 3, "duplicates": 3, "imported": 0, "matched_to_plans": []}
    assert all("another uploaded file" in d["reason"] for d in dry["duplicates"])
    assert dry["reconciliation"][0]["pnl_matches"] and dry["reconciliation"][0]["fees_match"]
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(JournalTrade)) == 0

    body["dry_run"] = False
    res = (await admin_client.post("/api/statements/import", json=body, headers=CSRF)).json()
    assert res["applied"] and res["trades"]["imported"] == 3
    assert len(res["trades"]["matched_to_plans"]) == 1  # the plan links to one trade (first long)
    assert res["balances"]["new"] == 1 and "Open positions recorded" in res["positions_note"]
    rec = res["reconciliation"][0]
    assert rec["cash_realized"] == 30.0 and rec["journal_gross"] == 30.0 and rec["pnl_matches"]
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(JournalTrade).order_by(JournalTrade.entry_time, JournalTrade.id))).all()
        assert [r.source for r in rows] == ["csv"] * 3 and round(sum(r.net_pnl for r in rows), 2) == 26.4
        linked = [r for r in rows if r.plan_id]
        assert len(linked) == 1 and linked[0].planned_stop == 4996 and linked[0].review is not None
        snaps = (await s.scalars(select(AccountSnapshot).order_by(AccountSnapshot.id))).all()
        eod = [x for x in snaps if x.is_eod and str(x.trading_date) == "2026-11-11"]
        assert len(eod) == 1 and float(eod[0].balance) == 50026.40
        pos = [x for x in snaps if not x.is_eod]
        assert pos and pos[-1].positions[0]["symbol"] == "ES" and float(pos[-1].unrealized_pnl) == 25.0

    again = (await admin_client.post("/api/statements/import", json=body, headers=CSRF)).json()
    assert again["trades"]["imported"] == 0 and again["trades"]["duplicates"] == 6
    assert again["balances"]["new"] == 0 and again["balances"]["conflicts"] == []
    conflict = CASH.replace("50026.40", "50030.00")
    c = (
        await admin_client.post(
            "/api/statements/import", json={"files": files(("cash2.csv", conflict)), "dry_run": True}, headers=CSRF
        )
    ).json()
    assert c["balances"]["conflicts"][0]["imported"] == "50030.00"


async def test_errors_block_the_whole_import_and_anonymous_cannot_import(admin_client: httpx.AsyncClient) -> None:
    from app.main import create_app

    body = {"files": files(("orders.csv", ORDERS), ("junk.csv", "foo,bar\n1,2\n")), "dry_run": False}
    r = (await admin_client.post("/api/statements/import", json=body, headers=CSRF)).json()
    assert r["blocked_by_errors"] and not r["applied"] and r["files"][1]["layout"] is None
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(JournalTrade)) == 0
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anon:
        r2 = await anon.post("/api/statements/import", json=body, headers=CSRF)
    assert r2.status_code in (401, 403)
