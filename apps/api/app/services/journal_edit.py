"""Journal editing with provenance and an audit trail.

Rules:

* Execution fields (times, symbol, direction, quantity, prices, fees, P&L) are the trade record.
  Before the first change, their values are copied once into ``JournalTrade.original`` and never
  modified again, so the broker-confirmed / originally recorded execution is always recoverable.
  Every changed execution field is listed in ``corrections`` with who changed it, when, and a
  provenance label (``manual correction of broker record`` for CSV imports).
* Annotation fields (strategy, setup, thesis, psychology, mistakes, lessons, tags, links, notes)
  are the trader's own words and are simply updated.
* Every change writes one ``JournalEdit`` row (field, old, new, user, time).
* Changing prices, quantity, direction or symbol recomputes gross and net P&L unless the P&L is
  given explicitly; changing what the review depends on clears the stored review so it is
  regenerated from the corrected record (journal, review and analytics stay in sync).
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.marketdata.calendar import trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.models.research import JournalEdit, JournalTrade
from app.services.journal import root_symbol

EXECUTION_FIELDS = (
    "trading_date",
    "entry_time",
    "exit_time",
    "symbol",
    "contract",
    "direction",
    "quantity",
    "entry_price",
    "exit_price",
    "fees",
    "gross_pnl",
    "net_pnl",
)
ANNOTATION_FIELDS = (
    "strategy_id",
    "setup",
    "planned_stop",
    "planned_target",
    "followed_plan",
    "mistakes",
    "notes",
    "thesis",
    "psychology",
    "emotion_tags",
    "lessons",
    "tags",
    "chart_links",
)
# Fields whose change invalidates the stored automatic review.
REVIEW_INPUTS = {*EXECUTION_FIELDS, "planned_stop", "planned_target", "strategy_id"}
PNL_INPUTS = {"entry_price", "exit_price", "quantity", "direction", "symbol"}
IMAGE_TYPES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class EditError(ValueError):
    """Invalid edit (reported as HTTP 422)."""


def jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 10)
    return v


def provenance(t: JournalTrade) -> str:
    return {
        "csv": "manual correction of broker record",
        "paper": "manual correction of simulated record",
    }.get(t.source, "manual correction")


def execution_snapshot(t: JournalTrade) -> dict[str, Any]:
    return {f: jsonable(getattr(t, f)) for f in EXECUTION_FIELDS}


def _validate(t: JournalTrade) -> None:
    sym = root_symbol(t.symbol)
    if sym is None or sym not in INSTRUMENTS:
        raise EditError(f"Unknown symbol {t.symbol!r}")
    t.symbol = sym
    if t.direction not in ("long", "short"):
        raise EditError("Direction must be long or short")
    if t.quantity <= 0:
        raise EditError("Quantity must be positive")
    if t.exit_time < t.entry_time:
        raise EditError("Exit time is before entry time")
    tick = float(INSTRUMENTS[sym].tick_size)
    for name in ("entry_price", "exit_price", "planned_stop", "planned_target"):
        px = getattr(t, name)
        if px is None:
            continue
        if px <= 0:
            raise EditError(f"{name.replace('_', ' ')} must be positive")
        if abs(round(px / tick) * tick - px) > 1e-6:
            raise EditError(f"{name.replace('_', ' ')} {px} is not on the {tick} tick grid for {sym}")
    if t.fees < 0:
        raise EditError("Fees cannot be negative")


async def apply_edit(
    session: AsyncSession, t: JournalTrade, changes: dict[str, Any], user_id: Any, user_email: str | None
) -> list[dict[str, Any]]:
    """Apply ``changes`` (already type-validated) and return the list of recorded changes."""
    unknown = set(changes) - set(EXECUTION_FIELDS) - set(ANNOTATION_FIELDS)
    if unknown:
        raise EditError(f"Fields cannot be edited: {', '.join(sorted(unknown))}")
    before = {f: jsonable(getattr(t, f)) for f in (*EXECUTION_FIELDS, *ANNOTATION_FIELDS)}
    requested = {k: v for k, v in changes.items() if jsonable(v) != before[k]}
    if not requested:
        return []
    if any(k in EXECUTION_FIELDS for k in requested) and t.original is None:
        t.original = execution_snapshot(t)  # written once, before the first correction
    for k, v in requested.items():
        setattr(t, k, v)
    # Derived execution values.
    if PNL_INPUTS & set(requested) and "gross_pnl" not in requested:
        inst = INSTRUMENTS.get(root_symbol(t.symbol) or "")
        if inst is not None:
            s = 1 if t.direction == "long" else -1
            t.gross_pnl = round((t.exit_price - t.entry_price) * s * float(inst.point_value) * t.quantity, 2)
    if "net_pnl" in requested and "gross_pnl" not in requested and not (PNL_INPUTS & set(requested)):
        t.gross_pnl = round(t.net_pnl + t.fees, 2)  # an explicit realised P&L keeps the fees
    elif {"gross_pnl", "fees", *PNL_INPUTS} & set(requested) and "net_pnl" not in requested:
        t.net_pnl = round(t.gross_pnl - t.fees, 2)
    if {"entry_time", "exit_time"} & set(requested) and "trading_date" not in requested:
        t.trading_date = trading_day(t.exit_time)
    _validate(t)

    now = datetime.now(UTC)
    after = {f: jsonable(getattr(t, f)) for f in before}
    recorded: list[dict[str, Any]] = []
    corrections = dict(t.corrections or {})
    for f in before:
        if after[f] == before[f]:
            continue
        derived = f not in requested
        is_exec = f in EXECUTION_FIELDS
        if is_exec:
            corrections[f] = {
                "value": after[f],
                "at": now.isoformat(),
                "by": user_email,
                "provenance": provenance(t),
                "derived": derived,
            }
        session.add(
            JournalEdit(
                trade_id=t.id,
                user_id=user_id,
                user_email=user_email,
                action="correct" if is_exec else "edit",
                field=f,
                old=before[f],
                new=after[f],
            )
        )
        recorded.append({"field": f, "old": before[f], "new": after[f], "derived": derived})
    if corrections != (t.corrections or {}):
        t.corrections = corrections
        t.corrected_at = now
    if REVIEW_INPUTS & {r["field"] for r in recorded}:
        t.review = None  # regenerated from the corrected record on next view / by the worker
        t.context = None
        t.reviewed_at = None
        t.mfe = t.mae = None
    await session.flush()
    return recorded


def full_snapshot(t: JournalTrade, notes: list[dict[str, Any]], attachments: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything needed to restore a deleted trade (attachment bytes are not kept)."""
    cols = {c.name: jsonable(getattr(t, c.key)) for c in JournalTrade.__table__.columns}
    return {"trade": cols, "notes": notes, "attachments": attachments}


def decode_image(data_b64: str, content_type: str) -> bytes:
    if content_type not in IMAGE_TYPES:
        raise EditError("Only PNG, JPEG or WebP screenshots are accepted")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except ValueError as exc:
        raise EditError("Attachment is not valid base64") from exc
    if not raw:
        raise EditError("Attachment is empty")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise EditError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
    magic = IMAGE_TYPES[content_type]
    if not raw.startswith(magic) or (content_type == "image/webp" and raw[8:12] != b"WEBP"):
        raise EditError("File content does not match its image type")
    return raw
