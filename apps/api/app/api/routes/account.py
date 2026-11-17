"""Apex rule profile, account snapshots and the Risk Guardian (deterministic, advisory)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.core.config import get_settings
from app.marketdata.instruments import INSTRUMENTS, get_instrument
from app.models.account import AccountSnapshot, RuleRow
from app.risk.engine import CostModel, check_trade
from app.risk.profile import PROFILE, PROFILE_LABEL, load_rules, parse_value, to_ruleset
from app.schemas.account import (
    CsvImportIn,
    CsvImportOut,
    RuleOut,
    RulesOut,
    RuleUpdateIn,
    RuleVerifyIn,
    SnapshotIn,
    SnapshotOut,
    TradeCheckIn,
)
from app.services.account import (
    current_state,
    default_trading_date,
    import_balances,
    parse_balance_csv,
)
from app.services.audit import record_audit

router = APIRouter(tags=["account"])

OFFICIAL_HOSTS = ("apextraderfunding.com", "support.apextraderfunding.com")
DISCLAIMER = (
    "Advisory only. Computed from user-entered account data and configured rule values; "
    "cannot guarantee protection against account failure."
)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _rules_out(rows: list[RuleRow]) -> RulesOut:
    rs = to_ruleset(rows)
    return RulesOut(
        profile=PROFILE,
        label=PROFILE_LABEL,
        verified=rs.verified,
        unverified=list(rs.unverified_rules),
        rules=[RuleOut.model_validate(r) for r in rows],
        warning=None
        if rs.verified
        else (
            f"{len(rs.unverified_rules)} required rule(s) unverified. No trade will be labelled compliant."
        ),
    )


# --- rules -----------------------------------------------------------------------------------


@router.get("/rules", response_model=RulesOut)
async def get_rules(_: CurrentUser, session: SessionDep) -> RulesOut:
    rows = await load_rules(session)
    await session.commit()
    return _rules_out(rows)


async def _rule(session: SessionDep, key: str) -> RuleRow:
    rows = await load_rules(session)
    row = next((r for r in rows if r.key == key), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown rule")
    return row


@router.put("/rules/{key}", response_model=RulesOut)
async def update_rule(
    key: str, body: RuleUpdateIn, user: AdminUser, request: Request, session: SessionDep
) -> RulesOut:
    """Change a rule value. Any change clears the rule's verification record."""
    row = await _rule(session, key)
    try:
        value = parse_value(row.kind, body.value, allow_blank=not row.required)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    old = row.value
    changed = value != old
    row.value = value
    if body.notes is not None:
        row.notes = body.notes
    if changed:
        row.verified, row.source_url, row.verified_at, row.verified_by = False, None, None, None
    await record_audit(
        session,
        action="rules.update",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"key": key, "old": old, "new": value},
    )
    await session.commit()
    return _rules_out(await load_rules(session))


@router.post("/rules/{key}/verify", response_model=RulesOut)
async def verify_rule(
    key: str, body: RuleVerifyIn, user: AdminUser, request: Request, session: SessionDep
) -> RulesOut:
    """Record that the current value was checked against an official source."""
    row = await _rule(session, key)
    host = (urlparse(body.source_url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Source must be an official Apex Trader Funding page (apextraderfunding.com).",
        )
    if row.required and not row.value.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Set a value before verifying it")
    row.verified, row.source_url, row.applies_to, row.version = (
        True,
        body.source_url,
        body.applies_to,
        body.version,
    )
    row.verified_at, row.verified_by = datetime.now(UTC), user.id
    if body.notes is not None:
        row.notes = body.notes
    await record_audit(
        session,
        action="rules.verify",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"key": key, "value": row.value, "source_url": body.source_url, "version": body.version},
    )
    await session.commit()
    return _rules_out(await load_rules(session))


@router.post("/rules/{key}/unverify", response_model=RulesOut)
async def unverify_rule(key: str, user: AdminUser, request: Request, session: SessionDep) -> RulesOut:
    row = await _rule(session, key)
    row.verified, row.source_url, row.verified_at, row.verified_by = False, None, None, None
    await record_audit(
        session,
        action="rules.unverify",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"key": key},
    )
    await session.commit()
    return _rules_out(await load_rules(session))


# --- account ---------------------------------------------------------------------------------


@router.get("/account/state")
async def get_state(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    rules, state = await current_state(session)
    await session.commit()
    return {
        "rules": _jsonable(asdict(rules)),
        "state": _jsonable(asdict(state)),
        "disclaimer": DISCLAIMER,
    }


@router.get("/account/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    _: CurrentUser, session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[SnapshotOut]:
    rows = (
        await session.scalars(
            select(AccountSnapshot)
            .order_by(AccountSnapshot.as_of.desc(), AccountSnapshot.id.desc())
            .limit(limit)
        )
    ).all()
    return [SnapshotOut.model_validate(r) for r in rows]


@router.post("/account/snapshots", response_model=SnapshotOut, status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    body: SnapshotIn, user: CurrentUser, request: Request, session: SessionDep
) -> SnapshotOut:
    now = datetime.now(UTC)
    as_of = body.as_of.astimezone(UTC) if body.as_of else now
    if (as_of - now).total_seconds() > 120:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "as_of is in the future")
    if body.is_eod and body.unrealized_pnl != 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "An end-of-day balance must have no unrealised P&L (positions closed).",
        )
    row = AccountSnapshot(
        as_of=as_of,
        source="manual",
        balance=body.balance,
        unrealized_pnl=body.unrealized_pnl,
        is_eod=body.is_eod,
        trading_date=body.trading_date or default_trading_date(as_of),
        positions=[
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_price": str(p.avg_price),
                "stop": str(p.stop) if p.stop is not None else None,
            }
            for p in body.positions
        ],
        note=body.note,
        created_by=user.id,
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        action="account.snapshot",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"id": row.id, "is_eod": body.is_eod},
    )
    await session.commit()
    await session.refresh(row)
    return SnapshotOut.model_validate(row)


@router.delete("/account/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot(
    snapshot_id: int, user: AdminUser, request: Request, session: SessionDep
) -> Response:
    row = await session.get(AccountSnapshot, snapshot_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Snapshot not found")
    await session.delete(row)
    await record_audit(
        session,
        action="account.snapshot_delete",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"id": snapshot_id, "balance": str(row.balance), "as_of": row.as_of.isoformat()},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/account/import-csv", response_model=CsvImportOut)
async def import_csv(
    body: CsvImportIn, user: CurrentUser, request: Request, session: SessionDep
) -> CsvImportOut:
    """Import end-of-day balances. Use ``dry_run`` to preview. Nothing is imported if any row fails."""
    res = parse_balance_csv(body.csv)
    imported = 0
    if not body.dry_run and res.imported and not res.errors:
        imported = await import_balances(session, res.imported, user.id)
        await record_audit(
            session,
            action="account.csv_import",
            outcome="success",
            actor_id=user.id,
            ip=client_ip(request),
            details={"rows": imported},
        )
        await session.commit()
    return CsvImportOut(
        dry_run=body.dry_run,
        imported=imported,
        rows=res.imported,
        errors=res.errors,
        date_column=res.date_column,
        balance_column=res.balance_column,
    )


# --- risk ------------------------------------------------------------------------------------


def cost_model_for(symbol: str) -> CostModel:
    s = get_settings()
    inst = INSTRUMENTS[symbol]
    per_side = s.cost_commission_per_side_micro if inst.is_micro else s.cost_commission_per_side_standard
    return CostModel(Decimal(str(per_side)), Decimal(str(s.cost_slippage_ticks_per_side)))


@router.post("/risk/check")
async def risk_check(body: TradeCheckIn, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    inst = get_instrument(body.symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    rules, state = await current_state(session)
    await session.commit()
    try:
        result = check_trade(
            rules,
            state,
            inst,
            body.direction,
            body.entry,
            body.stop,
            body.target,
            body.quantity,
            cost_model_for(inst.symbol),
            INSTRUMENTS,
            Decimal(str(get_settings().risk_max_trade_fraction)),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        "check": _jsonable(asdict(result)),
        "instrument": {
            "symbol": inst.symbol,
            "tick_size": float(inst.tick_size),
            "tick_value": float(inst.tick_value),
            "point_value": float(inst.point_value),
            "standard_equivalent": float(inst.standard_equivalent),
        },
        "cost_assumptions": _jsonable(asdict(cost_model_for(inst.symbol))),
        "account": _jsonable(
            {
                "known": state.known,
                "stale": state.stale,
                "risk_budget": state.risk_budget,
                "rules_verified": rules.verified,
            }
        ),
        "disclaimer": DISCLAIMER,
    }
