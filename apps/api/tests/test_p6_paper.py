from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.paper.engine import PaperBook, PaperConfig, new_state
from app.research.bars import Bar

T0 = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # Wed 10:00 ET
SUBMIT = T0 + timedelta(seconds=20)


def book(**cfg: float) -> PaperBook:
    b = PaperBook(new_state(PaperConfig(**cfg)))
    b.s["last_prices"]["ES"] = 5000.0
    return b


def bar(minute: int, o: float, h: float, low: float, c: float, v: int = 1000, start: datetime = T0) -> Bar:
    return Bar(start + timedelta(minutes=minute), o, h, low, c, v)


def test_market_order_fills_next_bar_open_with_slippage_and_commission() -> None:
    b = book()
    o = b.submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 2}, SUBMIT)
    assert o["status"] == "working"
    b.on_bar("ES", bar(0, 5000, 5001, 4999, 5000.5))  # opened before submission: not eligible
    assert o["status"] == "working"
    b.on_bar("ES", bar(1, 5001, 5002, 5000, 5001.5))
    assert o["status"] == "filled" and o["avg_fill_price"] == 5001.25  # open + 1 tick
    assert b.position("ES") == {"qty": 2, "avg_price": 5001.25, "opened_at": (T0 + timedelta(minutes=1)).isoformat()}
    assert b.s["balance"] == 50000 - 5.0  # 2 x $2.50
    assert b.unrealized() == pytest.approx((5001.5 - 5001.25) * 2 * 50)


def test_limit_requires_trade_through_and_stop_gaps() -> None:
    b = book()
    lim = b.submit({"symbol": "ES", "side": "buy", "type": "limit", "qty": 1, "price": 4995.0}, SUBMIT)
    b.on_bar("ES", bar(1, 4998, 4999, 4995.0, 4996))  # touched, not traded through
    assert lim["status"] == "working"
    b.on_bar("ES", bar(2, 4996, 4997, 4994.75, 4995))
    assert lim["status"] == "filled" and lim["avg_fill_price"] == 4995.0
    stp = b.submit(
        {"symbol": "ES", "side": "sell", "type": "stop", "qty": 1, "price": 4990.0},
        T0 + timedelta(minutes=2, seconds=30),
    )
    b.on_bar("ES", bar(3, 4985, 4986, 4984, 4985))  # gapped below the stop
    assert stp["status"] == "filled" and stp["avg_fill_price"] == 4984.75  # open - 1 tick
    closed = b.s["closed"][-1]
    assert closed["gross_pnl"] == pytest.approx((4984.75 - 4995.0) * 50) and closed["fees"] == 5.0
    assert b.position("ES")["qty"] == 0


def test_partial_fills_are_limited_by_bar_volume() -> None:
    b = book(participation=0.1)
    o = b.submit({"symbol": "MES", "side": "buy", "type": "market", "qty": 5}, SUBMIT)
    b.on_bar("MES", bar(1, 5000, 5001, 4999, 5000, v=20))  # cap = 2
    assert o["status"] == "partially_filled" and o["filled_qty"] == 2
    b.on_bar("MES", bar(2, 5001, 5002, 5000, 5001, v=30))  # cap = 3
    assert o["status"] == "filled" and o["filled_qty"] == 5
    assert o["avg_fill_price"] == pytest.approx((5000.25 * 2 + 5001.25 * 3) / 5)


def test_bracket_children_activate_and_cancel_each_other() -> None:
    b = book()
    entry = b.submit(
        {"symbol": "ES", "side": "buy", "type": "market", "qty": 1, "stop_loss": 4990.0, "take_profit": 5010.0}, SUBMIT
    )
    kids = [o for o in b.s["orders"] if o["parent_id"] == entry["id"]]
    assert {k["role"]: k["status"] for k in kids} == {"stop_loss": "pending", "take_profit": "pending"}
    b.on_bar("ES", bar(1, 5000, 5001, 4999, 5000))
    assert {k["status"] for k in kids} == {"working"} and all(k["qty"] == 1 for k in kids)
    b.on_bar("ES", bar(2, 5005, 5010.25, 5004, 5009))  # trades through the target
    tp = next(k for k in kids if k["role"] == "take_profit")
    sl = next(k for k in kids if k["role"] == "stop_loss")
    assert tp["status"] == "filled" and sl["status"] == "cancelled"
    assert b.position("ES")["qty"] == 0 and b.s["closed"][-1]["gross_pnl"] == pytest.approx((5010 - 5000.25) * 50)


@pytest.mark.parametrize(
    ("req", "reason"),
    [
        ({"symbol": "ZZZ", "side": "buy", "type": "market", "qty": 1}, "unknown symbol"),
        ({"symbol": "ES", "side": "buy", "type": "market", "qty": 0}, "quantity"),
        ({"symbol": "ES", "side": "buy", "type": "limit", "qty": 1, "price": 5000.1}, "tick size"),
        ({"symbol": "ES", "side": "buy", "type": "market", "qty": 7}, "6-contract limit"),
        ({"symbol": "MES", "side": "buy", "type": "market", "qty": 61}, "6-contract limit"),
        ({"symbol": "ES", "side": "buy", "type": "market", "qty": 1, "stop_loss": 5010.0}, "wrong side"),
        ({"symbol": "ES", "side": "sell", "type": "market", "qty": 1, "take_profit": 5010.0}, "wrong side"),
        ({"symbol": "ES", "side": "hold", "type": "market", "qty": 1}, "side must be"),
    ],
)
def test_rejections(req: dict, reason: str) -> None:
    o = book().submit(req, SUBMIT)
    assert o["status"] == "rejected" and reason in o["reason"]


def test_rejected_when_market_closed_and_contract_limit_counts_existing() -> None:
    saturday = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
    assert (
        "market is closed"
        in book().submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 1}, saturday)["reason"]
    )
    b = book()
    b.submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 5}, SUBMIT)
    b.on_bar("ES", bar(1, 5000, 5001, 4999, 5000))
    assert b.submit({"symbol": "MES", "side": "buy", "type": "market", "qty": 10}, SUBMIT)["status"] == "working"
    assert b.submit({"symbol": "MES", "side": "buy", "type": "market", "qty": 1}, SUBMIT)["status"] == "rejected"


def test_daily_loss_limit_flattens_and_halts_until_next_day() -> None:
    b = book()
    b.submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 2}, SUBMIT)
    b.on_bar("ES", bar(1, 5000, 5001, 4999, 5000))
    b.on_bar("ES", bar(2, 4995, 4996, 4989, 4990))  # -10.25 pts x 2 x $50 = -1,025
    assert b.s["status"] == "halted_daily_loss" and b.position("ES")["qty"] == 0
    assert b.submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 1}, SUBMIT)["status"] == "rejected"
    # Next trading day (after 18:00 ET) re-enables trading and records the EOD balance.
    b.on_bar("ES", bar(0, 4990, 4991, 4989, 4990, start=datetime(2026, 11, 11, 23, 0, tzinfo=UTC)))
    assert b.s["status"] == "active" and "2026-11-11" in b.s["eod_balances"]


def test_eod_drawdown_breach_is_permanent() -> None:
    b = book(daily_loss_limit=5000)
    b.submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 5}, SUBMIT)
    b.on_bar("ES", bar(1, 5000, 5001, 4999, 5000))
    b.on_bar("ES", bar(2, 4995, 4996, 4991, 4991.5))  # about -2,200
    assert b.s["status"] == "breached" and b.position("ES")["qty"] == 0
    b.on_bar("ES", bar(0, 4990, 4991, 4989, 4990, start=datetime(2026, 11, 11, 23, 0, tzinfo=UTC)))
    assert b.s["status"] == "breached"


def test_eod_threshold_trails_end_of_day_balances_only() -> None:
    b = book()
    b.s["eod_balances"] = {"2026-11-09": 50800.0, "2026-11-10": 50500.0}
    assert b.threshold() == 48800.0
    b.s["balance"] = 52000  # intraday gain does not move it
    assert b.threshold() == 48800.0


def test_end_of_session_flatten_cancel_and_duplicate_bars() -> None:
    b = book()
    o = b.submit({"symbol": "ES", "side": "buy", "type": "limit", "qty": 1, "price": 4900.0}, SUBMIT)
    b.cancel(o["id"], SUBMIT)
    assert o["status"] == "cancelled"
    with pytest.raises(ValueError, match="cancelled"):
        b.cancel(o["id"], SUBMIT)
    b.submit({"symbol": "ES", "side": "buy", "type": "market", "qty": 1}, SUBMIT)
    b.on_bar("ES", bar(1, 5000, 5001, 4999, 5000))
    fills = len(b.s["fills"])
    b.on_bar("ES", bar(1, 5000, 5001, 4999, 5000))  # duplicate bar ignored
    assert len(b.s["fills"]) == fills
    late = datetime(2026, 11, 11, 21, 58, tzinfo=UTC)  # 16:58 ET
    b.on_bar("ES", Bar(late, 5002, 5003, 5001, 5002, 100))
    assert b.position("ES")["qty"] == 0 and "end of session" in b.s["events"][-1]["text"]


def test_short_position_and_reversal_accounting() -> None:
    b = book()
    b.submit({"symbol": "NQ", "side": "sell", "type": "market", "qty": 1}, SUBMIT)
    b.on_bar("NQ", bar(1, 18000, 18001, 17990, 17995))
    assert b.position("NQ")["qty"] == -1 and b.position("NQ")["avg_price"] == 17999.75
    b.submit({"symbol": "NQ", "side": "buy", "type": "market", "qty": 1}, T0 + timedelta(minutes=1, seconds=30))
    b.on_bar("NQ", bar(2, 17990, 17991, 17980, 17985))
    assert b.position("NQ")["qty"] == 0
    assert b.s["closed"][-1]["gross_pnl"] == pytest.approx((17999.75 - 17990.25) * 20)


def test_paper_module_has_no_network_or_broker_access() -> None:
    src = (Path(__file__).resolve().parents[1] / "app" / "paper" / "engine.py").read_text()
    for banned in ("httpx", "requests", "aiohttp", "websocket", "socket", "urllib", "tradovate", "anthropic"):
        assert banned not in src.lower().replace("no network access", ""), banned
