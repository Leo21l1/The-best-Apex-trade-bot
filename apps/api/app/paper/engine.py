"""Isolated paper-trading simulator.

A pure, deterministic order book driven by 1-minute bars. It has no network access, no broker
client and no knowledge of any real account; it can only change its own JSON state. Fill rules are
deliberately conservative:

* market: next bar's open plus adverse slippage;
* limit: only when the bar trades THROUGH the limit by at least one tick (at the limit, or at the
  open if the bar opened better);
* stop: when the bar trades at or through the trigger, at the trigger or the open if it gapped
  beyond, plus adverse slippage;
* partial fills: at most ``participation`` x bar volume (minimum 1 contract) fills per bar;
* brackets: stop-loss and take-profit children activate when the entry fills and cancel each other.

Risk limits mirror the configured Apex values: an order that would exceed the contract limit is
rejected; when the day's loss reaches the daily limit, or equity reaches the EOD drawdown threshold,
open positions are closed at that bar's close and new orders are rejected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from app.marketdata.calendar import ET, is_market_open, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.research.bars import Bar

FLATTEN_AT = time(16, 58)  # positions closed before the 17:00 ET session end (conservative)
ORDER_TYPES = ("market", "limit", "stop")
SIDES = ("buy", "sell")


class OrderRejected(ValueError):
    pass


@dataclass(frozen=True)
class PaperConfig:
    starting_balance: float = 50_000.0
    max_drawdown: float = 2_000.0
    daily_loss_limit: float = 1_000.0
    max_contracts: float = 6.0
    commission_standard: float = 2.50
    commission_micro: float = 0.60
    slippage_ticks: float = 1.0
    participation: float = 0.10


def new_state(cfg: PaperConfig) -> dict[str, Any]:
    return {
        "config": cfg.__dict__.copy(),
        "balance": cfg.starting_balance,
        "eod_balances": {},
        "current_day": None,
        "day_start_balance": cfg.starting_balance,
        "status": "active",  # active | halted_daily_loss | breached
        "orders": [],
        "positions": {},
        "fills": [],
        "closed": [],
        "last_prices": {},
        "last_bar_ts": {},
        "next_id": 1,
        "events": [],
    }


class PaperBook:
    def __init__(self, state: dict[str, Any]) -> None:
        self.s = state
        self.cfg = PaperConfig(**state["config"])

    # -- helpers ------------------------------------------------------------------------------
    def _id(self) -> int:
        i = self.s["next_id"]
        self.s["next_id"] = i + 1
        return i

    def _event(self, ts: datetime, text: str) -> None:
        self.s["events"].append({"ts": ts.isoformat(), "text": text})
        del self.s["events"][:-200]

    def _commission(self, symbol: str, qty: int) -> float:
        per = self.cfg.commission_micro if INSTRUMENTS[symbol].is_micro else self.cfg.commission_standard
        return round(per * qty, 2)

    def position(self, symbol: str) -> dict[str, Any]:
        return self.s["positions"].get(symbol, {"qty": 0, "avg_price": 0.0, "opened_at": None})

    def exposure(self) -> float:
        return sum(
            abs(p["qty"]) * float(INSTRUMENTS[sym].standard_equivalent) for sym, p in self.s["positions"].items()
        )

    def unrealized(self) -> float:
        total = 0.0
        for sym, p in self.s["positions"].items():
            last = self.s["last_prices"].get(sym)
            if p["qty"] and last is not None:
                total += (last - p["avg_price"]) * p["qty"] * float(INSTRUMENTS[sym].point_value)
        return round(total, 2)

    def threshold(self) -> float:
        high = max([self.cfg.starting_balance, *self.s["eod_balances"].values()])
        return high - self.cfg.max_drawdown

    def summary(self) -> dict[str, Any]:
        equity = round(self.s["balance"] + self.unrealized(), 2)
        return {
            "status": self.s["status"],
            "balance": round(self.s["balance"], 2),
            "unrealized": self.unrealized(),
            "equity": equity,
            "drawdown_threshold": self.threshold(),
            "drawdown_room": round(equity - self.threshold(), 2),
            "day_pnl": round(equity - self.s["day_start_balance"], 2),
            "daily_loss_remaining": round(self.cfg.daily_loss_limit + min(equity - self.s["day_start_balance"], 0), 2),
            "contracts_in_use": self.exposure(),
            "max_contracts": self.cfg.max_contracts,
            "profit_target_note": "Evaluation target tracking uses your configured rule profile values.",
        }

    # -- orders -------------------------------------------------------------------------------
    def submit(self, req: dict[str, Any], now: datetime) -> dict[str, Any]:
        """Validate and accept an order (or return it with status 'rejected' and a reason)."""
        symbol = str(req.get("symbol", "")).upper()
        order = {
            "id": self._id(),
            "symbol": symbol,
            "side": req.get("side"),
            "type": req.get("type"),
            "qty": int(req.get("qty", 0) or 0),
            "filled_qty": 0,
            "price": req.get("price"),
            "status": "working",
            "reason": None,
            "created_at": now.isoformat(),
            "parent_id": None,
            "role": "entry",
            "oco": None,
            "avg_fill_price": None,
            "plan_id": req.get("plan_id"),
        }
        try:
            self._validate(order, req, now)
        except OrderRejected as exc:
            order["status"], order["reason"] = "rejected", str(exc)
            self.s["orders"].append(order)
            self._event(now, f"Order {order['id']} rejected: {exc}")
            return order
        self.s["orders"].append(order)
        # Bracket children wait until the entry fills.
        for role, key, typ in (("stop_loss", "stop_loss", "stop"), ("take_profit", "take_profit", "limit")):
            if req.get(key) is not None:
                self.s["orders"].append(
                    {
                        "id": self._id(),
                        "symbol": symbol,
                        "side": "sell" if order["side"] == "buy" else "buy",
                        "type": typ,
                        "qty": 0,
                        "filled_qty": 0,
                        "price": float(req[key]),
                        "status": "pending",
                        "reason": None,
                        "created_at": now.isoformat(),
                        "parent_id": order["id"],
                        "role": role,
                        "oco": order["id"],
                        "avg_fill_price": None,
                    }
                )
        self._event(now, f"Order {order['id']} accepted: {order['side']} {order['qty']} {symbol} {order['type']}")
        return order

    def _validate(self, order: dict[str, Any], req: dict[str, Any], now: datetime) -> None:
        inst = INSTRUMENTS.get(order["symbol"])
        if inst is None:
            raise OrderRejected("unknown symbol")
        if order["side"] not in SIDES or order["type"] not in ORDER_TYPES:
            raise OrderRejected("side must be buy/sell and type market/limit/stop")
        if order["qty"] < 1:
            raise OrderRejected("quantity must be at least 1")
        if self.s["status"] != "active":
            raise OrderRejected(f"account is {self.s['status'].replace('_', ' ')}")
        if not is_market_open(now):
            raise OrderRejected("market is closed")
        tick = float(inst.tick_size)

        def on_tick(name: str, v: Any) -> float:
            try:
                f = float(v)
            except (TypeError, ValueError) as exc:
                raise OrderRejected(f"{name} must be a number") from exc
            if f <= 0 or not math.isclose(round(f / tick) * tick, f, abs_tol=1e-9):
                raise OrderRejected(f"{name} must be a positive multiple of the tick size {inst.tick_size}")
            return f

        if order["type"] != "market":
            order["price"] = on_tick("price", order["price"])
        else:
            order["price"] = None
        s = 1 if order["side"] == "buy" else -1
        ref = order["price"] or self.s["last_prices"].get(order["symbol"])
        for key in ("stop_loss", "take_profit"):
            if req.get(key) is not None:
                v = on_tick(key, req[key])
                if ref is not None and ((ref - v) * s <= 0 if key == "stop_loss" else (v - ref) * s <= 0):
                    raise OrderRejected(f"{key.replace('_', ' ')} is on the wrong side of the entry")
        pos = self.position(order["symbol"])["qty"]
        working = sum(
            o["qty"] - o["filled_qty"]
            for o in self.s["orders"]
            if o["symbol"] == order["symbol"]
            and o["status"] == "working"
            and o["role"] == "entry"
            and (1 if o["side"] == "buy" else -1) == s
        )
        resulting = abs(pos + s * (working + order["qty"]))
        others = self.exposure() - abs(pos) * float(inst.standard_equivalent)
        if others + resulting * float(inst.standard_equivalent) > self.cfg.max_contracts + 1e-9:
            raise OrderRejected(f"would exceed the {self.cfg.max_contracts:g}-contract limit (micros count as 0.1)")

    def cancel(self, order_id: int, now: datetime) -> dict[str, Any]:
        for o in self.s["orders"]:
            if o["id"] == order_id:
                if o["status"] not in ("working", "pending", "partially_filled"):
                    raise OrderRejected(f"order is {o['status']}")
                o["status"] = "cancelled"
                for child in self.s["orders"]:
                    if child["parent_id"] == order_id and child["status"] == "pending":
                        child["status"] = "cancelled"
                self._event(now, f"Order {order_id} cancelled")
                return o
        raise OrderRejected("order not found")

    # -- bar processing -----------------------------------------------------------------------
    def on_bar(self, symbol: str, bar: Bar) -> None:
        """Process one closed 1-minute bar for ``symbol``. Bars must arrive in time order."""
        last = self.s["last_bar_ts"].get(symbol)
        if last is not None and bar.ts.isoformat() <= last:
            return  # duplicate or out-of-order bar: ignore
        self.s["last_bar_ts"][symbol] = bar.ts.isoformat()
        self._roll_day(bar.ts)
        inst = INSTRUMENTS[symbol]
        tick = float(inst.tick_size)
        slip = self.cfg.slippage_ticks * tick
        cap = max(1, int(bar.volume * self.cfg.participation))
        for o in list(self.s["orders"]):
            if o["symbol"] != symbol or o["status"] not in ("working", "partially_filled"):
                continue
            if bar.ts < datetime.fromisoformat(o["created_at"]).replace(second=0, microsecond=0) or (
                bar.ts.isoformat() <= o["created_at"] and o["role"] == "entry"
            ):
                continue  # only bars that open after submission
            s = 1 if o["side"] == "buy" else -1
            px = None
            if o["type"] == "market":
                px = bar.open + s * slip
            elif o["type"] == "limit":
                lim = o["price"]
                through = bar.low <= lim - tick if s == 1 else bar.high >= lim + tick
                if through:
                    px = min(lim, bar.open) if s == 1 else max(lim, bar.open)
            else:  # stop
                trig = o["price"]
                hit = bar.high >= trig if s == 1 else bar.low <= trig
                if hit:
                    px = (max(trig, bar.open) if s == 1 else min(trig, bar.open)) + s * slip
            if px is None:
                continue
            ref = bar.open if o["type"] == "market" else o["price"]
            qty = min(o["qty"] - o["filled_qty"], cap)
            if o["role"] != "entry":
                qty = min(qty, abs(self.position(symbol)["qty"]))  # exits never reverse the position
                if qty <= 0:
                    o["status"] = "cancelled"
                    continue
            cap -= qty
            self._fill(o, bar.ts, round(round(px / tick) * tick, 6), qty, ref if o["type"] != "limit" else None)
            if cap <= 0:
                break
        self.s["last_prices"][symbol] = bar.close
        self._risk_checks(symbol, bar)

    def on_open(self, symbol: str, ts: datetime, open_price: float) -> None:
        """Fill eligible MARKET orders at the open of a bar that has just started.

        A bar's open is final as soon as the bar begins, so market orders need not wait for the bar to
        close. Limit and stop orders still wait for the closed bar (its high/low are not yet known).
        No liquidity cap is applied here because the bar's volume is not yet known.
        """
        last = self.s["last_bar_ts"].get(symbol)
        if last is not None and ts.isoformat() <= last:
            return
        self._roll_day(ts)
        tick = float(INSTRUMENTS[symbol].tick_size)
        slip = self.cfg.slippage_ticks * tick
        for o in list(self.s["orders"]):
            if (
                o["symbol"] != symbol
                or o["type"] != "market"
                or o["role"] != "entry"
                or o["status"] not in ("working", "partially_filled")
                or ts.isoformat() <= o["created_at"]
            ):
                continue
            s = 1 if o["side"] == "buy" else -1
            px = round(round((open_price + s * slip) / tick) * tick, 6)
            self._fill(o, ts, px, o["qty"] - o["filled_qty"], open_price)
        self.s["last_prices"][symbol] = open_price

    def _fill(self, o: dict[str, Any], ts: datetime, px: float, qty: int, ref: float | None = None) -> None:
        symbol = o["symbol"]
        pv = float(INSTRUMENTS[symbol].point_value)
        slippage = round(abs(px - ref) * pv * qty, 2) if ref is not None else 0.0
        s = 1 if o["side"] == "buy" else -1
        commission = self._commission(symbol, qty)
        prev_avg = o["avg_fill_price"] or 0.0
        o["avg_fill_price"] = round((prev_avg * o["filled_qty"] + px * qty) / (o["filled_qty"] + qty), 6)
        o["filled_qty"] += qty
        o["status"] = "filled" if o["filled_qty"] >= o["qty"] else "partially_filled"
        self.s["fills"].append(
            {
                "order_id": o["id"],
                "ts": ts.isoformat(),
                "symbol": symbol,
                "side": o["side"],
                "qty": qty,
                "price": px,
                "commission": commission,
                "slippage": slippage,
            }
        )
        self.s["balance"] -= commission
        plan_id = o.get("plan_id") or next(
            (p.get("plan_id") for p in self.s["orders"] if p["id"] == o.get("parent_id")), None
        )
        self._apply(symbol, s * qty, px, ts, commission, slippage, plan_id)
        if o["role"] == "entry":
            for child in self.s["orders"]:
                if child["parent_id"] == o["id"] and child["status"] in ("pending", "working"):
                    child["qty"] = o["filled_qty"]
                    child["status"] = "working"
                    child["created_at"] = ts.isoformat()
        elif o["oco"] is not None:
            for sib in self.s["orders"]:
                if sib["oco"] == o["oco"] and sib["id"] != o["id"] and sib["status"] in ("working", "pending"):
                    if o["status"] == "filled":
                        sib["status"] = "cancelled"
                    else:
                        sib["qty"] = max(sib["filled_qty"], sib["qty"] - qty)
        self._event(ts, f"Filled {o['side']} {qty} {symbol} @ {px} (order {o['id']})")

    def _apply(
        self,
        symbol: str,
        signed_qty: int,
        px: float,
        ts: datetime,
        commission: float,
        slippage: float = 0.0,
        plan_id: Any = None,
    ) -> None:
        pos = self.position(symbol)
        pv = float(INSTRUMENTS[symbol].point_value)
        q, avg = pos["qty"], pos["avg_price"]
        if q == 0 or (q > 0) == (signed_qty > 0):
            new_q = q + signed_qty
            pos = {
                "qty": new_q,
                "avg_price": round((avg * abs(q) + px * abs(signed_qty)) / abs(new_q), 6),
                "opened_at": pos["opened_at"] or ts.isoformat(),
                "entry_slippage": round(pos.get("entry_slippage", 0.0) + slippage, 2),
                "plan_id": pos.get("plan_id") or plan_id,
            }
        else:
            closing = min(abs(q), abs(signed_qty))
            direction = 1 if q > 0 else -1
            gross = (px - avg) * direction * closing * pv
            self.s["balance"] += gross
            self.s["closed"].append(
                {
                    "id": len(self.s["closed"]) + 1,
                    "symbol": symbol,
                    "direction": "long" if q > 0 else "short",
                    "qty": closing,
                    "entry_time": pos["opened_at"],
                    "exit_time": ts.isoformat(),
                    "entry_price": avg,
                    "exit_price": px,
                    "gross_pnl": round(gross, 2),
                    # Entry commission was charged on the opening fill; attribute both sides to the round trip.
                    "fees": round(self._commission(symbol, closing) * 2, 2),
                    # Slippage is already inside the fill prices; reported separately for review.
                    "slippage": round(pos.get("entry_slippage", 0.0) * closing / abs(q) + slippage, 2),
                    "plan_id": pos.get("plan_id") or plan_id,
                }
            )
            remaining = q + signed_qty
            if remaining == 0:
                pos = {"qty": 0, "avg_price": 0.0, "opened_at": None}
            elif (remaining > 0) == (q > 0):
                pos = {
                    **pos,
                    "qty": remaining,
                    "entry_slippage": round(pos.get("entry_slippage", 0.0) * remaining / abs(q), 2),
                }
            else:  # reversed through zero
                pos = {
                    "qty": remaining,
                    "avg_price": px,
                    "opened_at": ts.isoformat(),
                    "entry_slippage": 0.0,
                    "plan_id": plan_id,
                }
        if pos["qty"] == 0:
            self.s["positions"].pop(symbol, None)
        else:
            self.s["positions"][symbol] = pos

    def _flatten(self, ts: datetime, reason: str, prices: dict[str, float] | None = None) -> None:
        for sym, p in list(self.s["positions"].items()):
            px = (prices or {}).get(sym, self.s["last_prices"].get(sym))
            if px is None or not p["qty"]:
                continue
            qty = abs(p["qty"])
            commission = self._commission(sym, qty)
            self.s["balance"] -= commission
            self.s["fills"].append(
                {
                    "order_id": None,
                    "ts": ts.isoformat(),
                    "symbol": sym,
                    "side": "sell" if p["qty"] > 0 else "buy",
                    "qty": qty,
                    "price": px,
                    "commission": commission,
                }
            )
            self._apply(sym, -p["qty"], px, ts, commission)
        for o in self.s["orders"]:
            if o["status"] in ("working", "pending", "partially_filled"):
                o["status"] = "cancelled"
                o["reason"] = reason
        self._event(ts, f"Flattened all positions: {reason}")

    def flatten(self, ts: datetime) -> None:
        self._flatten(ts, "flattened by user")

    def _risk_checks(self, symbol: str, bar: Bar) -> None:
        if self.s["status"] != "active":
            return
        local = bar.ts.astimezone(ET).time()
        if local >= FLATTEN_AT and self.s["positions"]:
            self._flatten(bar.ts, "end of session (16:58 ET)")
        equity = self.s["balance"] + self.unrealized()
        if equity <= self.threshold():
            self._flatten(bar.ts, "EOD drawdown threshold reached")
            self.s["status"] = "breached"
        elif equity - self.s["day_start_balance"] <= -self.cfg.daily_loss_limit:
            self._flatten(bar.ts, "daily loss limit reached")
            self.s["status"] = "halted_daily_loss"

    def _roll_day(self, ts: datetime) -> None:
        day = trading_day(ts).isoformat()
        cur = self.s["current_day"]
        if cur is None:
            self.s["current_day"] = day
            return
        if day != cur:
            # Balance at the end of the previous trading day becomes its EOD balance.
            self.s["eod_balances"][cur] = round(self.s["balance"], 2)
            self.s["current_day"] = day
            self.s["day_start_balance"] = self.s["balance"]
            if self.s["status"] == "halted_daily_loss":
                self.s["status"] = "active"
