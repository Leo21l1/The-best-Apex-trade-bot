"""Broker connectivity status and the manual statement import (read-only, no broker access).

There is deliberately no Tradovate login, token, API client or page automation in PROJECT APEX:
see ``BROKER_POLICY`` and docs/compliance/broker-connectivity.md.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.core.config import get_settings
from app.marketdata.instruments import INSTRUMENTS
from app.models.account import AccountSnapshot
from app.models.research import JournalTrade, TradePlan
from app.services.account import default_trading_date, import_balances, parse_balance_csv
from app.services.audit import record_audit
from app.services.broker_import import detect_layout, parse_cash, parse_fills, parse_positions, same_trade
from app.services.journal import ParsedTrade, parse_trades_csv, to_row
from app.services.trade_review import review_trade

router = APIRouter(prefix="/statements", tags=["statements"])

POLICY_CHECKED = "2026-11-17"
BROKER_POLICY: dict[str, Any] = {
    "checked_on": POLICY_CHECKED,
    "api_sync_permitted": False,
    "api_sync_implemented": False,
    "summary": "Automatic read-only API synchronisation is not enabled: neither Tradovate nor Apex documents "
    "permission for API access on Apex evaluation accounts. Use the statement import instead.",
    "findings": [
        {
            "source": "Tradovate support: Tradovate API Access",
            "url": "https://support.tradovate.com/s/article/Tradovate-API-Access?language=en_US",
            "finding": "Prop firm and evaluation accounts are not eligible for API access; a live funded Tradovate "
            "account (minimum $1,000) plus the paid API Access add-on is required.",
        },
        {
            "source": "Apex Trader Funding: Prohibited Activities / PA compliance",
            "url": "https://apextraderfunding.com/help-center/getting-started/prohibited-activities/",
            "finding": "No automation or algorithm usage; Apex encourages submitting software for approval before "
            "use. No page grants API or third-party data access for evaluation accounts.",
        },
        {
            "source": "Apex Trader Funding: Tradovate API Request Limit",
            "url": "https://apextraderfunding.com/help-center/tradovate/tradovate-api-request-limit/",
            "finding": "Only describes Tradovate's request limits; it does not authorise API use.",
        },
    ],
    "not_done": [
        "No Tradovate login, credentials or API keys are requested or stored.",
        "No order submission, cancellation or modification exists.",
        "No browser automation, scraping or unofficial endpoints.",
    ],
    "to_enable": "Written confirmation from Apex (and Tradovate API eligibility for the account) that read-only "
    "API access is permitted; a separate, reviewed read-only adapter would then be needed.",
    "manual_workflow": [
        "In Tradovate open Reports (Account Reports) for your Apex account and pick a date range.",
        "Export the Performance (or Orders/Fills) report, the Cash History, and optionally Positions as CSV.",
        "Upload the files here together. Duplicates are skipped, trades are matched to your plans and reviewed, "
        "end-of-day balances update the risk engine, and totals are reconciled against the cash history.",
    ],
}


@router.get("/policy")
async def policy(_: CurrentUser) -> dict[str, Any]:
    return BROKER_POLICY


class FileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)
    csv: str = Field(max_length=2_000_000)


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: Annotated[list[FileIn], Field(min_length=1, max_length=10)]
    timezone: str = "America/New_York"
    dry_run: bool = True


async def match_plan(session: SessionDep, t: JournalTrade) -> TradePlan | None:
    """Most recent unlinked plan (same symbol and direction) written in the 8 hours before entry."""
    taken = select(JournalTrade.plan_id).where(JournalTrade.plan_id.is_not(None))
    return await session.scalar(
        select(TradePlan)
        .where(
            TradePlan.symbol == t.symbol,
            TradePlan.direction == t.direction,
            TradePlan.status.in_(("executed_manual", "draft")),
            TradePlan.created_at <= t.entry_time,
            TradePlan.created_at >= t.entry_time - timedelta(hours=8),
            TradePlan.id.not_in(taken),
        )
        .order_by(TradePlan.created_at.desc())
        .limit(1)
    )


@router.post("/import")
async def import_statements(body: ImportIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown time zone") from exc
    s = get_settings()
    fee = {"standard": s.cost_commission_per_side_standard, "micro": s.cost_commission_per_side_micro}
    files_out: list[dict[str, Any]] = []
    trades: list[tuple[str, ParsedTrade]] = []
    cash_days: list[dict[str, Any]] = []
    balances: list[tuple[Any, Decimal]] = []
    positions: dict[str, Any] | None = None
    open_lots: list[dict[str, Any]] = []
    warnings: list[str] = []
    any_errors = False

    for f in body.files:
        first = f.csv.lstrip("﻿").splitlines()[0] if f.csv.strip() else ""
        header = next(csv.reader([first]), []) if first else []
        layout = detect_layout(header)
        out: dict[str, Any] = {"name": f.name, "layout": layout, "errors": []}
        if layout in ("performance", "generic"):
            r = parse_trades_csv(f.csv, body.timezone, fee)
            out.update(trades=len(r.trades), errors=r.errors[:30])
            trades += [(f.name, t) for t in r.trades]
            if not any(h.strip().lower() in ("fees", "commission") for h in header):
                warnings.append(f"{f.name}: no commission column; fees are estimated from the configured rates.")
        elif layout == "fills":
            r = parse_fills(f.csv, body.timezone, fee)
            out.update(
                fills=r.fills,
                trades=len(r.trades),
                skipped_unfilled=r.skipped_unfilled,
                open_lots=len(r.open_lots),
                errors=r.errors[:30],
            )
            trades += [(f.name, t) for t in r.trades]
            open_lots += r.open_lots
            if r.fees_estimated:
                warnings.append(f"{f.name}: some fills have no commission; fees estimated from the configured rates.")
        elif layout == "cash":
            r = parse_cash(f.csv, body.timezone)
            out.update(rows=r.rows, days=len(r.days), errors=r.errors[:30])
            cash_days += r.days
            if not r.has_balance:
                warnings.append(f"{f.name}: no balance column, so end-of-day balances cannot be updated from it.")
            balances += [
                (datetime.fromisoformat(d["date"]).date(), Decimal(f"{d['end_balance']:.2f}"))
                for d in r.days
                if d["end_balance"] is not None
            ]
        elif layout == "positions":
            r = parse_positions(f.csv)
            out.update(positions=len(r.positions), errors=r.errors[:30])
            positions = {"rows": r.positions, "open_pnl": r.open_pnl}
        elif layout == "balances":
            r = parse_balance_csv(f.csv)
            out.update(rows=len(r.imported), errors=r.errors[:30])
            balances += list(r.imported)
        else:
            out["errors"] = [
                "Unrecognised report. Supported: Tradovate Performance, Orders/Fills, Cash History, "
                "Positions, end-of-day balances, or the generic trade layout. Header: " + ", ".join(header[:12])
            ]
        any_errors = any_errors or bool(out["errors"])
        files_out.append(out)

    # --- duplicate detection: exact key, within this batch, and against stored trades -------
    dupes: list[dict[str, Any]] = []
    unique: list[ParsedTrade] = []
    keys = [t.source_key for _, t in trades]
    existing_keys = (
        set((await session.scalars(select(JournalTrade.source_key).where(JournalTrade.source_key.in_(keys)))).all())
        if keys
        else set()
    )
    stored: list[JournalTrade] = []
    if trades:
        lo = min(t.entry_time for _, t in trades) - timedelta(days=1)
        hi = max(t.exit_time for _, t in trades) + timedelta(days=1)
        stored = list(
            (
                await session.scalars(
                    select(JournalTrade).where(
                        JournalTrade.source.in_(("csv", "manual")),
                        JournalTrade.entry_time >= lo,
                        JournalTrade.entry_time <= hi,
                    )
                )
            ).all()
        )
    for name, t in trades:
        tick = float(INSTRUMENTS[t.symbol].tick_size)
        if t.source_key in existing_keys:
            dupes.append(
                {
                    "file": name,
                    "reason": "already imported (same report row)",
                    "symbol": t.symbol,
                    "entry_time": t.entry_time.isoformat(),
                }
            )
            continue
        twin = next((u for u in unique if same_trade(t, u, tick)), None)
        if twin is not None:
            dupes.append(
                {
                    "file": name,
                    "reason": "same execution appears in another uploaded file",
                    "symbol": t.symbol,
                    "entry_time": t.entry_time.isoformat(),
                }
            )
            continue
        prior = next((x for x in stored if same_trade(t, x, tick)), None)
        if prior is not None:
            dupes.append(
                {
                    "file": name,
                    "reason": f"matches stored {prior.source} trade #{prior.id}",
                    "symbol": t.symbol,
                    "entry_time": t.entry_time.isoformat(),
                    "trade_id": prior.id,
                }
            )
            continue
        unique.append(t)

    # --- balances: skip dates already recorded; report conflicting values ---------------------
    new_balances: list[tuple[Any, Decimal]] = []
    conflicts: list[dict[str, Any]] = []
    if balances:
        dates = sorted({d for d, _ in balances})
        have = {
            (r.trading_date, r.balance)
            for r in (
                await session.scalars(
                    select(AccountSnapshot).where(
                        AccountSnapshot.is_eod.is_(True), AccountSnapshot.trading_date.in_(dates)
                    )
                )
            ).all()
        }
        have_dates = {d for d, _ in have}
        latest: dict[Any, Decimal] = {}
        for d, b in balances:
            latest[d] = b
        for d, b in sorted(latest.items()):
            if (d, b) in have:
                continue
            if d in have_dates:
                conflicts.append(
                    {"date": d.isoformat(), "imported": str(b), "stored": [str(x) for dd, x in have if dd == d]}
                )
                continue
            new_balances.append((d, b))

    imported: list[JournalTrade] = []
    matched: list[dict[str, Any]] = []
    snapshot_note = None
    if not body.dry_run and not any_errors:
        for t in unique:
            row = to_row(t, "csv")
            session.add(row)
            imported.append(row)
        await session.flush()
        for row in imported:
            plan = await match_plan(session, row)
            if plan is not None:
                row.plan_id = plan.id
                row.planned_stop, row.planned_target = plan.stop, plan.target
                row.strategy_id = row.strategy_id or plan.strategy_id
                matched.append({"trade_id": row.id, "plan_id": plan.id})
                await session.flush()
        for row in imported[:100]:
            await review_trade(session, row, s)  # the worker reviews any remainder
        if new_balances:
            await import_balances(session, new_balances, user.id)
        if positions and positions["rows"]:
            last = await session.scalar(select(AccountSnapshot).order_by(AccountSnapshot.as_of.desc()).limit(1))
            if last is None:
                snapshot_note = "Positions not applied: enter a balance first (no account snapshot exists)."
            else:
                session.add(
                    AccountSnapshot(
                        as_of=datetime.now(UTC),
                        source="csv",
                        balance=last.balance,
                        unrealized_pnl=Decimal(str(positions["open_pnl"] or 0)),
                        is_eod=False,
                        trading_date=default_trading_date(datetime.now(UTC)),
                        created_by=user.id,
                        positions=[
                            {
                                "symbol": p["symbol"],
                                "quantity": p["quantity"],
                                "avg_price": str(p["avg_price"]),
                                "stop": None,
                            }
                            for p in positions["rows"]
                        ],
                        note="Positions CSV import; balance carried from the snapshot of "
                        f"{last.as_of:%Y-%m-%d %H:%M} UTC",
                    )
                )
                snapshot_note = f"Open positions recorded with the balance from {last.as_of:%Y-%m-%d %H:%M} UTC" + (
                    "" if positions["open_pnl"] is not None else "; open P&L not in the file (set 0)."
                )
        await record_audit(
            session,
            action="broker.import",
            outcome="success",
            actor_id=user.id,
            ip=client_ip(request),
            details={"trades": len(imported), "balances": len(new_balances), "files": [f["layout"] for f in files_out]},
        )
        await session.commit()

    # --- reconciliation against cash history (per trading date) ------------------------------
    recon: list[dict[str, Any]] = []
    if cash_days:
        for d in cash_days:
            day = datetime.fromisoformat(d["date"]).date()
            journal = list(
                (
                    await session.scalars(
                        select(JournalTrade).where(JournalTrade.trading_date == day, JournalTrade.source == "csv")
                    )
                ).all()
            )
            pending = [t for t in unique if t.exit_time and _tday(t.exit_time) == day] if body.dry_run else []
            gross = sum(t.gross_pnl for t in journal) + sum(t.gross_pnl for t in pending)
            fees = sum(t.fees for t in journal) + sum(t.fees for t in pending)
            cash_fees = -(d["commission"] + d["fee"])
            recon.append(
                {
                    "date": d["date"],
                    "cash_realized": d["realized"],
                    "journal_gross": round(gross, 2),
                    "cash_fees": round(cash_fees, 2),
                    "journal_fees": round(fees, 2),
                    "pnl_matches": abs(d["realized"] - gross) < 0.01,
                    "fees_match": abs(cash_fees - fees) < 0.01,
                    "end_balance": d["end_balance"],
                }
            )

    return {
        "dry_run": body.dry_run,
        "applied": not body.dry_run and not any_errors,
        "blocked_by_errors": any_errors,
        "files": files_out,
        "trades": {
            "parsed": len(trades),
            "new": len(unique),
            "duplicates": len(dupes),
            "imported": len(imported),
            "matched_to_plans": matched,
        },
        "duplicates": dupes[:100],
        "balances": {"parsed": len(balances), "new": len(new_balances), "conflicts": conflicts},
        "positions": positions,
        "positions_note": snapshot_note,
        "open_lots_from_fills": open_lots,
        "reconciliation": recon,
        "warnings": warnings,
        "preview": [
            {
                "symbol": t.symbol,
                "contract": t.contract,
                "direction": t.direction,
                "qty": t.quantity,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "gross_pnl": t.gross_pnl,
                "fees": t.fees,
                "net_pnl": round(t.gross_pnl - t.fees, 2),
            }
            for t in unique[:20]
        ],
    }


def _tday(ts: datetime) -> Any:
    from app.marketdata.calendar import trading_day

    return trading_day(ts)
