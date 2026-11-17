"""Journal editing: corrections with provenance, annotations, notes, screenshots, audit, delete/restore."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.models.research import JournalAttachment, JournalEdit, JournalNote, JournalTrade
from app.services.audit import record_audit
from app.services.journal import MISTAKE_TAGS
from app.services.journal_edit import (
    EditError,
    apply_edit,
    decode_image,
    full_snapshot,
    jsonable,
)
from app.strategies.library import STRATEGY_BY_ID

router = APIRouter(tags=["journal"])

PgInt = Annotated[int, PathParam(ge=1, le=2_147_483_647)]
_TAG = re.compile(r"^[\w .+/-]{1,32}$")


def _tags(v: list[str] | None, what: str) -> list[str] | None:
    if v is None:
        return v
    clean = sorted({x.strip() for x in v if x.strip()})
    bad = [x for x in clean if not _TAG.match(x)]
    if bad:
        raise ValueError(f"invalid {what}: {', '.join(bad[:3])}")
    return clean


class TradeEdit(BaseModel):
    """Any subset of editable fields. Omitted fields are unchanged; null clears an optional field."""

    model_config = ConfigDict(extra="forbid")
    # execution record (corrections are tracked with provenance)
    trading_date: date | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    symbol: str | None = Field(default=None, max_length=16)
    contract: str | None = Field(default=None, max_length=16)
    direction: Literal["long", "short"] | None = None
    quantity: int | None = Field(default=None, ge=1, le=1000)
    entry_price: float | None = Field(default=None, gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0, le=100_000)
    gross_pnl: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    net_pnl: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    # annotations
    strategy_id: str | None = None
    setup: str | None = Field(default=None, max_length=64)
    planned_stop: float | None = Field(default=None, gt=0)
    planned_target: float | None = Field(default=None, gt=0)
    followed_plan: bool | None = None
    mistakes: list[str] | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=5000)
    thesis: str | None = Field(default=None, max_length=5000)
    psychology: str | None = Field(default=None, max_length=5000)
    emotion_tags: list[str] | None = Field(default=None, max_length=12)
    lessons: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=20)
    chart_links: list[str] | None = Field(default=None, max_length=10)

    @field_validator("entry_time", "exit_time")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps must include a time zone")
        return v.astimezone(UTC) if v else v

    @field_validator("mistakes")
    @classmethod
    def _mistakes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        bad = [m for m in v if m not in MISTAKE_TAGS]
        if bad:
            raise ValueError(f"unknown mistake tags: {', '.join(bad)}")
        return sorted(set(v))

    @field_validator("strategy_id")
    @classmethod
    def _strategy(cls, v: str | None) -> str | None:
        if v not in (None, "", *STRATEGY_BY_ID, "discretionary"):
            raise ValueError("unknown strategy")
        return v or None

    @field_validator("emotion_tags")
    @classmethod
    def _emotions(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v, "emotion tags")

    @field_validator("tags")
    @classmethod
    def _tagsv(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v, "tags")

    @field_validator("chart_links")
    @classmethod
    def _links(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        out = [x.strip() for x in v if x.strip()]
        for x in out:
            if not re.match(r"^https?://[^\s]{3,500}$", x):
                raise ValueError("chart links must be http(s) URLs")
        return out


NON_NULLABLE = {
    "trading_date",
    "entry_time",
    "exit_time",
    "symbol",
    "direction",
    "quantity",
    "entry_price",
    "exit_price",
    "fees",
    "gross_pnl",
    "net_pnl",
    "mistakes",
    "emotion_tags",
    "tags",
    "chart_links",
}


async def _trade(session: AsyncSession, trade_id: int) -> JournalTrade:
    t = await session.get(JournalTrade, trade_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Trade #{trade_id} not found")
    return t


@router.patch("/journal/trades/{trade_id}")
async def edit_trade(
    trade_id: PgInt, body: TradeEdit, user: AdminUser, request: Request, session: SessionDep
) -> dict[str, Any]:
    from app.api.routes.terminal import trade_detail

    t = await _trade(session, trade_id)
    changes = body.model_dump(exclude_unset=True)
    nulls = [k for k, v in changes.items() if v is None and k in NON_NULLABLE]
    if nulls:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Cannot clear: {', '.join(nulls)}")
    try:
        recorded = await apply_edit(session, t, changes, user.id, user.email)
    except EditError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if recorded:
        await record_audit(
            session,
            action="journal.edit",
            outcome="success",
            actor_id=user.id,
            ip=client_ip(request),
            details={"trade_id": trade_id, "fields": [r["field"] for r in recorded]},
        )
    await session.commit()
    await session.refresh(t)
    out = trade_detail(t)
    out["changes"] = recorded
    return out


@router.get("/journal/trades/{trade_id}/history")
async def history(trade_id: PgInt, _: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(JournalEdit)
            .where(JournalEdit.trade_id == trade_id)
            .order_by(JournalEdit.at.desc(), JournalEdit.id.desc())
            .limit(500)
        )
    ).all()
    return [
        {
            "id": r.id,
            "at": r.at.isoformat(),
            "user": r.user_email,
            "action": r.action,
            "field": r.field,
            "old": r.old if r.action != "delete" else None,
            "new": r.new,
        }
        for r in rows
    ]


# --- notes -------------------------------------------------------------------------------------


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=5000)


def note_out(n: JournalNote) -> dict[str, Any]:
    return {
        "id": n.id,
        "trade_id": n.trade_id,
        "body": n.body,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


@router.get("/journal/trades/{trade_id}/notes")
async def list_notes(trade_id: PgInt, _: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    await _trade(session, trade_id)
    rows = (
        await session.scalars(
            select(JournalNote).where(JournalNote.trade_id == trade_id).order_by(JournalNote.created_at)
        )
    ).all()
    return [note_out(n) for n in rows]


@router.post("/journal/trades/{trade_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(trade_id: PgInt, body: NoteIn, user: AdminUser, session: SessionDep) -> dict[str, Any]:
    await _trade(session, trade_id)
    n = JournalNote(trade_id=trade_id, author_id=user.id, body=body.body.strip())
    session.add(n)
    await session.flush()
    session.add(
        JournalEdit(
            trade_id=trade_id,
            user_id=user.id,
            user_email=user.email,
            action="note_add",
            field=f"note:{n.id}",
            old=None,
            new=n.body,
        )
    )
    await session.commit()
    await session.refresh(n)
    return note_out(n)


async def _note(session: AsyncSession, note_id: int) -> JournalNote:
    n = await session.get(JournalNote, note_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Note #{note_id} not found")
    return n


@router.patch("/journal/notes/{note_id}")
async def edit_note(note_id: PgInt, body: NoteIn, user: AdminUser, session: SessionDep) -> dict[str, Any]:
    n = await _note(session, note_id)
    old = n.body
    n.body = body.body.strip()
    n.updated_at = datetime.now(UTC)
    session.add(
        JournalEdit(
            trade_id=n.trade_id,
            user_id=user.id,
            user_email=user.email,
            action="note_edit",
            field=f"note:{n.id}",
            old=old,
            new=n.body,
        )
    )
    await session.commit()
    await session.refresh(n)
    return note_out(n)


@router.delete("/journal/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: PgInt, user: AdminUser, session: SessionDep) -> Response:
    n = await _note(session, note_id)
    session.add(
        JournalEdit(
            trade_id=n.trade_id,
            user_id=user.id,
            user_email=user.email,
            action="note_delete",
            field=f"note:{n.id}",
            old=n.body,
            new=None,
        )
    )
    await session.delete(n)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- screenshots ---------------------------------------------------------------------------------


class AttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=200)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    data_base64: str = Field(max_length=7_100_000)
    caption: str | None = Field(default=None, max_length=500)


def attachment_out(a: JournalAttachment) -> dict[str, Any]:
    return {
        "id": a.id,
        "trade_id": a.trade_id,
        "filename": a.filename,
        "content_type": a.content_type,
        "size": a.size,
        "caption": a.caption,
        "created_at": a.created_at.isoformat(),
        "url": f"/api/journal/attachments/{a.id}",
    }


@router.get("/journal/trades/{trade_id}/attachments")
async def list_attachments(trade_id: PgInt, _: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    await _trade(session, trade_id)
    rows = (
        await session.scalars(
            select(JournalAttachment)
            .where(JournalAttachment.trade_id == trade_id)
            .order_by(JournalAttachment.created_at)
        )
    ).all()
    return [attachment_out(a) for a in rows]


@router.post("/journal/trades/{trade_id}/attachments", status_code=status.HTTP_201_CREATED)
async def add_attachment(trade_id: PgInt, body: AttachmentIn, user: AdminUser, session: SessionDep) -> dict[str, Any]:
    await _trade(session, trade_id)
    try:
        raw = decode_image(body.data_base64, body.content_type)
    except EditError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    name = re.sub(r"[^\w.\- ]", "_", body.filename)[:200]
    a = JournalAttachment(
        trade_id=trade_id,
        author_id=user.id,
        filename=name,
        content_type=body.content_type,
        size=len(raw),
        caption=body.caption,
        data=raw,
    )
    session.add(a)
    await session.flush()
    session.add(
        JournalEdit(
            trade_id=trade_id,
            user_id=user.id,
            user_email=user.email,
            action="attachment_add",
            field=f"attachment:{a.id}",
            old=None,
            new=name,
        )
    )
    await session.commit()
    await session.refresh(a)
    return attachment_out(a)


@router.get("/journal/attachments/{attachment_id}")
async def get_attachment(attachment_id: PgInt, _: CurrentUser, session: SessionDep) -> Response:
    a = await session.get(JournalAttachment, attachment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return Response(
        content=a.data,
        media_type=a.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{a.filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/journal/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(attachment_id: PgInt, user: AdminUser, session: SessionDep) -> Response:
    a = await session.get(JournalAttachment, attachment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    session.add(
        JournalEdit(
            trade_id=a.trade_id,
            user_id=user.id,
            user_email=user.email,
            action="attachment_delete",
            field=f"attachment:{a.id}",
            old=a.filename,
            new=None,
        )
    )
    await session.delete(a)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- delete with confirmation, and restore ------------------------------------------------------


@router.delete("/journal/trades/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trade(
    trade_id: PgInt,
    user: AdminUser,
    request: Request,
    session: SessionDep,
    confirm: Annotated[int | None, Query(description="Must repeat the trade id")] = None,
) -> Response:
    t = await _trade(session, trade_id)
    if confirm != trade_id:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED, f"Confirm deletion by repeating the trade id (?confirm={trade_id})."
        )
    notes = [
        note_out(n) for n in (await session.scalars(select(JournalNote).where(JournalNote.trade_id == trade_id))).all()
    ]
    atts = [
        {k: v for k, v in attachment_out(a).items() if k != "url"}
        for a in (await session.scalars(select(JournalAttachment).where(JournalAttachment.trade_id == trade_id))).all()
    ]
    session.add(
        JournalEdit(
            trade_id=trade_id,
            user_id=user.id,
            user_email=user.email,
            action="delete",
            field=None,
            old=full_snapshot(t, notes, atts),
            new=None,
        )
    )
    await session.delete(t)
    await record_audit(
        session,
        action="journal.delete",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"trade_id": trade_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/journal/deleted")
async def deleted(_: AdminUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(JournalEdit).where(JournalEdit.action == "delete").order_by(JournalEdit.at.desc()).limit(100)
        )
    ).all()
    out = []
    for r in rows:
        if await session.get(JournalTrade, r.trade_id) is not None:
            continue  # already restored
        tr = (r.old or {}).get("trade", {})
        out.append(
            {
                "edit_id": r.id,
                "trade_id": r.trade_id,
                "deleted_at": r.at.isoformat(),
                "by": r.user_email,
                "symbol": tr.get("symbol"),
                "direction": tr.get("direction"),
                "net_pnl": tr.get("net_pnl"),
                "exit_time": tr.get("exit_time"),
                "attachments_lost": len((r.old or {}).get("attachments", [])),
            }
        )
    return out


def _parse(col: Any, v: Any) -> Any:
    if v is None:
        return None
    name = type(col.type).__name__
    if name == "DateTime":
        return datetime.fromisoformat(v)
    if name == "Date":
        return date.fromisoformat(v)
    return v


@router.post("/journal/deleted/{edit_id}/restore")
async def restore(edit_id: PgInt, user: AdminUser, session: SessionDep) -> dict[str, Any]:
    from app.api.routes.terminal import trade_detail

    r = await session.get(JournalEdit, edit_id)
    if r is None or r.action != "delete" or not r.old:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No deleted trade with that id")
    if await session.get(JournalTrade, r.trade_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Trade already exists (already restored?)")
    snap = r.old["trade"]
    cols = {c.name: c for c in JournalTrade.__table__.columns}
    values = {k: _parse(cols[k], v) for k, v in snap.items() if k in cols}
    t = JournalTrade(**{JournalTrade.__mapper__.get_property_by_column(cols[k]).key: v for k, v in values.items()})
    session.add(t)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A trade with the same source record exists again") from exc
    for n in r.old.get("notes", []):
        session.add(JournalNote(trade_id=t.id, body=n["body"], created_at=datetime.fromisoformat(n["created_at"])))
    session.add(
        JournalEdit(
            trade_id=t.id,
            user_id=user.id,
            user_email=user.email,
            action="restore",
            field=None,
            old=None,
            new={"from_edit": edit_id},
        )
    )
    await session.commit()
    await session.refresh(t)
    return trade_detail(t)


__all__ = ["jsonable", "router"]
