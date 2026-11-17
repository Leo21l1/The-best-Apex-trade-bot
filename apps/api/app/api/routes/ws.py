"""Authenticated WebSocket channel.

Phase 1 publishes periodic ``system.status`` frames and answers pings. Later phases add
market-data, scanner and alert topics over the same channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.deps import resolve_user
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services.health import system_status

router = APIRouter()
log = logging.getLogger("apex.ws")
STATUS_INTERVAL_S = 5.0


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    # Non-browser clients send no Origin; browsers always do, so enforce when present.
    return origin is None or origin in get_settings().cors_origins


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not _origin_allowed(ws):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    token = ws.cookies.get(get_settings().cookie_name)
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    async with get_sessionmaker()() as session:
        user = await resolve_user(session, token)
    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    seq = 0

    async def send(topic: str, data: object) -> None:
        nonlocal seq
        seq += 1
        await ws.send_json(
            {
                "topic": topic,
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(),
                "data": data,
            }
        )

    async def publisher() -> None:
        while True:
            st = await system_status()
            await send("system.status", st.model_dump(mode="json"))
            await asyncio.sleep(STATUS_INTERVAL_S)

    task = asyncio.create_task(publisher())
    try:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await send("pong", {"echo": msg.get("id")})
            else:
                await send("error", {"detail": "unsupported message"})
    except WebSocketDisconnect:
        pass
    except (ValueError, RuntimeError) as exc:  # malformed JSON / closed socket
        log.info("ws closed: %s", type(exc).__name__)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
