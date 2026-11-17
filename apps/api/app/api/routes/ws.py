"""Authenticated WebSocket channel.

Frames: ``{topic, seq, ts, data}``. Topics:

* ``system.status`` - pushed to every client every few seconds.
* ``alerts`` - pushed to every client when an alert is created.
* ``candles:<SYMBOL>`` - 1-minute bar updates relayed from the ingestion worker (Redis
  pub/sub). Clients opt in with ``{"type": "subscribe", "topics": ["candles:ES"]}`` and
  leave with ``{"type": "unsubscribe", ...}``.

The channel is strictly server-to-client data plus subscription control. It carries no order
or account-mutation messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from redis.exceptions import RedisError

from app.api.deps import resolve_user
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.feed import CANDLE_CHANNEL
from app.marketdata.instruments import get_instrument
from app.services.alerts import ALERT_CHANNEL
from app.services.health import system_status

router = APIRouter()
log = logging.getLogger("apex.ws")
STATUS_INTERVAL_S = 5.0


def _valid_topic(topic: str) -> bool:
    kind, _, symbol = topic.partition(":")
    return kind == "candles" and get_instrument(symbol) is not None and symbol == symbol.upper()


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

    user_id = user.id

    async def still_authorised() -> bool:
        """Re-check the session so logout, deactivation or a password change closes the socket."""
        async with get_sessionmaker()() as session:
            current = await resolve_user(session, token)
        return current is not None and current.id == user_id

    async def publisher() -> None:
        while True:
            if not await still_authorised():
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            st = await system_status()
            await send("system.status", st.model_dump(mode="json"))
            await asyncio.sleep(STATUS_INTERVAL_S)

    subscribed: set[str] = set()

    async def relay() -> None:
        """Forward market-data events for subscribed topics; resubscribe if Redis drops."""
        while True:
            pubsub = get_redis().pubsub()
            try:
                await pubsub.subscribe(CANDLE_CHANNEL, ALERT_CHANNEL)
                while True:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    if msg is None:
                        continue
                    try:
                        data = json.loads(msg["data"])
                        if msg["channel"] == ALERT_CHANNEL:
                            await send("alerts", data)  # alerts go to every client
                            continue
                        topic = f"candles:{data['symbol']}"
                    except (ValueError, KeyError, TypeError):
                        continue
                    if topic in subscribed:
                        await send(topic, data)
            except (RedisError, OSError):
                await asyncio.sleep(2.0)
            finally:
                with contextlib.suppress(RedisError, OSError):
                    await pubsub.aclose()

    tasks = [asyncio.create_task(publisher()), asyncio.create_task(relay())]
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "ping":
                await send("pong", {"echo": msg.get("id")})
            elif kind in ("subscribe", "unsubscribe"):
                topics = msg.get("topics")
                valid = (
                    {t for t in topics if isinstance(t, str) and _valid_topic(t)} if isinstance(topics, list) else set()
                )
                if kind == "subscribe":
                    subscribed.update(valid)
                else:
                    subscribed.difference_update(valid)
                await send("subscribed", {"topics": sorted(subscribed)})
            else:
                await send("error", {"detail": "unsupported message"})
    except WebSocketDisconnect:
        pass
    except (ValueError, RuntimeError) as exc:  # malformed JSON / closed socket
        log.info("ws closed: %s", type(exc).__name__)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
