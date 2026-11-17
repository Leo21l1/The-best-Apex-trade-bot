"""Live Trade Entry Confirmation and Save Trade Plan (analysis only: never places an order)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.marketdata.instruments import get_instrument
from app.models.research import TradePlan
from app.services.audit import record_audit
from app.services.confirmation import confirmation

router = APIRouter(prefix="/confirmation", tags=["confirmation"])

Style = Literal["intraday", "scalping", "momentum", "orb", "afternoon"]


def _sym(symbol: str) -> str:
    inst = get_instrument(symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    return inst.symbol


@router.get("")
async def get_confirmation(
    _: CurrentUser, session: SessionDep, symbol: str = "ES", style: Style = "intraday"
) -> dict[str, Any]:
    return await confirmation(session, _sym(symbol), style=style)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    style: Style = "intraday"
    quantity: int = Field(default=1, ge=1, le=50)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/plan", status_code=status.HTTP_201_CREATED)
async def save_plan(body: PlanIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    """Store the current confirmation as a draft trade plan. No order is created or sent."""
    sym = _sym(body.symbol)
    c = await confirmation(session, sym, style=body.style)
    lv = c.get("levels")
    if not lv:
        raise HTTPException(status.HTTP_409_CONFLICT, f"No setup levels to save ({c['state']}): {c['summary']}")
    if c.get("max_size") is not None and body.quantity > c["max_size"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Quantity {body.quantity} exceeds the Risk Guardian maximum of {c['max_size']}.",
        )
    risk = next((x["evidence"] for x in c["conditions"] if x["key"] == "risk"), None)
    plan = TradePlan(
        symbol=sym,
        direction=lv["direction"],
        entry=lv["entry"],
        stop=lv["stop"],
        target=lv["t1"],
        quantity=body.quantity,
        strategy_id=c.get("strategy_id"),
        thesis=(f"[{c['state']}] {c['summary']}" + (f"\n\n{body.notes}" if body.notes else ""))[:4000],
        signal_snapshot={"label": c["state"], "reasons": [m["reason"] for m in c["missing"]], "confirmation": c},
        risk_snapshot=risk,
        status="draft",
        created_by=user.id,
    )
    session.add(plan)
    await session.flush()
    await record_audit(
        session,
        action="plan.create",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"plan_id": plan.id, "source": "confirmation", "state": c["state"]},
    )
    await session.commit()
    return {"id": plan.id, "state": c["state"], "levels": lv, "terminal_url": "/terminal", "order_sent": False}
