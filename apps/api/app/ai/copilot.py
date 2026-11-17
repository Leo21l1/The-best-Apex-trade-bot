"""AI trading copilot: Claude with controlled, read-only application tools.

The loop is hosted here (Messages API) because every tool reads the local database. Numerical
risk decisions are never produced by the model: it must call ``check_trade_risk`` /
``get_account_risk``, which run the deterministic engine. The model has no tool that can place,
modify or cancel orders.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from app.ai.tools import ToolContext, run_tool, tool_definitions
from app.core.config import Settings
from app.core.logging import log_event
from app.marketdata.calendar import ET

log = logging.getLogger("apex.copilot")

#: Models that accept server-side refusal fallbacks (fallbacks="default").
_FALLBACK_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_HISTORY_TURNS = 20
MAX_RESULT_CHARS_FOR_UI = 4000

SYSTEM_PROMPT = """You are the PROJECT APEX trading copilot: an analysis assistant for one futures trader \
working an Apex Trader Funding $50K End-of-Day (EOD) Evaluation on Tradovate. The trader places and manages \
every order manually. You cannot place, modify or cancel orders and must never imply that you can or will.

Ground every factual statement in tool results from this conversation. Before answering about prices, \
setups, levels, the account, rules, events or alerts, call the relevant tools. Never invent or estimate \
prices, balances, fills, P&L, news, event times, win rates or expectancy. If a tool says data is \
unavailable, say plainly that it is missing and what would provide it.

Risk numbers (dollar risk, position size, drawdown room, daily loss remaining, rule violations) must come \
from get_account_risk or check_trade_risk. Do not compute them yourself. If the rule profile is unverified \
or account data is stale or unknown, say so and never call a trade compliant.

Be honest about evidence. No strategy has been validated by backtesting, so there is no historical \
expectancy or probability for any setup; setup scores are an uncalibrated checklist, not probabilities. \
Regimes and indicators describe the past. Do not claim an economic event caused a market move. If market \
data is SIMULATED, say clearly that the prices are synthetic and not real market conditions.

Structure answers with these headings, including only those that apply:
**Verified data** - figures from tools, each with its source (tool, provider, data class, timestamp).
**Estimates** - indicator readings, setup scores and other derived metrics, labelled as such.
**Interpretation** - your reasoning about what the data may mean, clearly marked as opinion.
**Missing information** - what you could not find and why it matters.

Be concise and specific: short paragraphs or bullets, prices with the instrument's precision, times in \
New York (ET). This is analysis, not financial advice; the trader decides."""


@dataclass
class CopilotResult:
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    fallback_used: bool = False
    note: str | None = None


class CopilotUnavailable(Exception):
    """Raised when the copilot cannot run (no API key, rejected key, provider failure)."""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


def build_client(settings: Settings, http_client: Any | None = None) -> AsyncAnthropic:
    if settings.anthropic_api_key is None:
        raise CopilotUnavailable("AI copilot is not configured: set APEX_ANTHROPIC_API_KEY on the server.")
    return AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.copilot_timeout_seconds,
        max_retries=2,
        **({"http_client": http_client} if http_client is not None else {}),
    )


def _history(turns: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Prior turns as plain text (user/assistant alternating, starting with user)."""
    out: list[dict[str, Any]] = []
    for t in turns[-MAX_HISTORY_TURNS:]:
        role, text = t.get("role"), (t.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        if not out and role != "user":
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    if out and out[-1]["role"] == "user":
        out.pop()  # the new question follows; a dangling user turn would merge into it
    return out


def _clip(result: dict[str, Any]) -> Any:
    text = json.dumps(result, default=str)
    return result if len(text) <= MAX_RESULT_CHARS_FOR_UI else {"truncated_for_display": text[:MAX_RESULT_CHARS_FOR_UI]}


async def ask(
    client: AsyncAnthropic,
    settings: Settings,
    ctx: ToolContext,
    question: str,
    history: list[dict[str, str]] | None = None,
) -> CopilotResult:
    now_et = ctx.now.astimezone(ET)
    messages: list[dict[str, Any]] = [
        *_history(history or []),
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                # Volatile context goes in the user turn, after the cached system prompt and tools.
                {"type": "text", "text": f"(Current time: {now_et:%A %Y-%m-%d %H:%M} ET)"},
            ],
        },
    ]
    extra: dict[str, Any] = {}
    if settings.copilot_model in _FALLBACK_MODELS:
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}

    result = CopilotResult(answer="")
    tools = tool_definitions()
    started = time.monotonic()
    for round_no in range(settings.copilot_max_tool_rounds + 1):
        try:
            response = await client.beta.messages.create(
                model=settings.copilot_model,
                max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM_PROMPT}],
                tools=tools,
                messages=messages,
                output_config={"effort": settings.copilot_effort},
                cache_control={"type": "ephemeral"},
                **extra,
            )
        except anthropic.AuthenticationError as exc:
            raise CopilotUnavailable("The Anthropic API key was rejected.", 502) from exc
        except anthropic.PermissionDeniedError as exc:
            raise CopilotUnavailable("The Anthropic API key lacks permission for this model.", 502) from exc
        except anthropic.RateLimitError as exc:
            raise CopilotUnavailable("Anthropic rate limit reached. Try again shortly.", 503) from exc
        except anthropic.BadRequestError as exc:
            raise CopilotUnavailable(f"The AI request was rejected: {exc.message}", 502) from exc
        except anthropic.APIStatusError as exc:
            raise CopilotUnavailable(f"AI provider error ({exc.status_code}). Try again later.", 502) from exc
        except anthropic.APIConnectionError as exc:
            raise CopilotUnavailable("Could not reach the AI provider.", 503) from exc

        usage = response.usage
        result.input_tokens += usage.input_tokens or 0
        result.output_tokens += usage.output_tokens or 0
        result.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        result.model = response.model
        result.stop_reason = response.stop_reason
        if any(getattr(b, "type", None) == "fallback" for b in response.content):
            result.fallback_used = True

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            cat = getattr(details, "category", None)
            result.answer = "The AI model declined to answer this request" + (f" (category: {cat})." if cat else ".")
            break
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason != "tool_use":
            result.answer = "\n\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
            if response.stop_reason == "max_tokens":
                result.note = "The answer was cut off at the length limit."
            break
        if round_no == settings.copilot_max_tool_rounds:
            result.answer = "\n\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
            result.note = f"Stopped after {settings.copilot_max_tool_rounds} tool rounds."
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        # Tools share one database session, so they run sequentially; all results go back in one message.
        for block in (b for b in response.content if getattr(b, "type", None) == "tool_use"):
            output, is_error = await run_tool(ctx, block.name, block.input)
            result.tool_calls.append(
                {"name": block.name, "input": block.input, "is_error": is_error, "result": _clip(output)}
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str),
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    if not result.answer:
        result.answer = "No answer was produced."
    log_event(
        log,
        "copilot answered",
        model=result.model,
        stop_reason=result.stop_reason,
        tools=[c["name"] for c in result.tool_calls],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read=result.cache_read_tokens,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return result


def now_et(dt: datetime) -> str:
    return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
