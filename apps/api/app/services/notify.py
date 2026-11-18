"""Phone notifications (ntfy, Pushover or Telegram). Optional, best effort, never blocks trading logic.

* Configured only through environment variables (.env); secrets are SecretStr and never logged.
* ``send`` never raises: a failed push is logged (without the URL or token) and returns False.
* Short timeout and no retries: a notification must never delay the scanner or the autopilot.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import log_event

log = logging.getLogger("apex.notify")
SEVERITY = {"info": 0, "warning": 1, "critical": 2}
TIMEOUT_S = 5.0


def enabled() -> bool:
    return get_settings().notify_provider != "none"


def _secret(x: Any) -> str:
    return x.get_secret_value() if x is not None else ""


async def send(title: str, body: str, severity: str = "info", client: httpx.AsyncClient | None = None) -> bool:
    s = get_settings()
    if s.notify_provider == "none" or SEVERITY.get(severity, 0) < SEVERITY[s.notify_min_severity]:
        return False
    title, body = title[:200], body[:1500]
    own = client is None
    c = client or httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        if s.notify_provider == "ntfy":
            if not s.notify_ntfy_url:
                raise ValueError("APEX_NOTIFY_NTFY_URL is not set")
            headers = {
                "Title": title.encode("ascii", "replace").decode(),
                "Priority": {"info": "default", "warning": "high", "critical": "urgent"}[severity],
                "Tags": "chart_with_upwards_trend",
            }
            if _secret(s.notify_ntfy_token):
                headers["Authorization"] = f"Bearer {_secret(s.notify_ntfy_token)}"
            r = await c.post(s.notify_ntfy_url, content=body.encode(), headers=headers)
        elif s.notify_provider == "pushover":
            r = await c.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": _secret(s.notify_pushover_token),
                    "user": _secret(s.notify_pushover_user),
                    "title": title,
                    "message": body,
                    "priority": {"info": 0, "warning": 1, "critical": 1}[severity],
                },
            )
        else:
            token = _secret(s.notify_telegram_bot_token)
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": s.notify_telegram_chat_id, "text": f"{title}\n{body}"},
            )
        if r.status_code >= 400:
            log_event(
                log, "notification rejected", level=logging.WARNING, provider=s.notify_provider, status=r.status_code
            )
            return False
        return True
    except (httpx.HTTPError, ValueError) as exc:
        log_event(
            log, "notification failed", level=logging.WARNING, provider=s.notify_provider, error=type(exc).__name__
        )
        return False
    finally:
        if own:
            await c.aclose()
