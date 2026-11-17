"""Copilot loop tests against a mocked Anthropic Messages API (no network, no API key needed)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from pydantic import SecretStr
from sqlalchemy import select

from app.ai.copilot import SYSTEM_PROMPT, _history, ask
from app.ai.tools import TOOLS, ToolContext, run_tool, tool_definitions
from app.api.routes.copilot import get_copilot_client
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.main import create_app
from app.models.account import AccountSnapshot
from app.models.audit import AuditLog
from app.models.copilot import CopilotExchange
from tests.conftest import ADMIN_EMAIL, CSRF, STRONG_PASSWORD


def msg(content: list[dict[str, Any]], stop: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 50,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 0,
        },
        **extra,
    }


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def tool_use(i: str, name: str, inp: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


class FakeAnthropic:
    """Serves scripted responses and records every request body and header."""

    def __init__(self, responses: list[dict[str, Any] | Callable[[dict], httpx2.Response]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        nxt = self.responses.pop(0)
        if callable(nxt):
            return nxt(body)
        return httpx2.Response(200, json=nxt)

    def client(self) -> AsyncAnthropic:
        http = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.handler))
        return AsyncAnthropic(api_key="test-key", http_client=http, max_retries=0)


async def ctx() -> ToolContext:
    session = get_sessionmaker()()
    return ToolContext(session=session, settings=get_settings(), now=datetime.now(UTC))


async def seed_account() -> None:
    async with get_sessionmaker()() as s:
        s.add(
            AccountSnapshot(
                as_of=datetime.now(UTC),
                source="manual",
                balance=Decimal("50000"),
                unrealized_pnl=Decimal("0"),
                is_eod=False,
                trading_date=datetime.now(UTC).date(),
                positions=[],
            )
        )
        await s.commit()


# --- the loop ------------------------------------------------------------------------------


async def test_tool_loop_request_shape_and_deterministic_numbers() -> None:
    await seed_account()
    fake = FakeAnthropic(
        [
            msg(
                [
                    text("Checking."),
                    tool_use("t1", "get_account_risk", {}),
                    tool_use(
                        "t2",
                        "check_trade_risk",
                        {
                            "symbol": "ES",
                            "direction": "long",
                            "entry": 5000,
                            "stop": 4996,
                            "target": 5008,
                            "quantity": 1,
                        },
                    ),
                ],
                "tool_use",
            ),
            msg([text("**Verified data**\nLoss at stop after costs: $230.00 (check_trade_risk).")]),
        ]
    )
    c = await ctx()
    try:
        result = await ask(fake.client(), get_settings(), c, "What is the risk of a 1-lot ES long 5000/4996?")
    finally:
        await c.session.close()

    assert result.answer.startswith("**Verified data**")
    assert [t["name"] for t in result.tool_calls] == ["get_account_risk", "check_trade_risk"]
    assert not any(t["is_error"] for t in result.tool_calls)
    check = result.tool_calls[1]["result"]["check"]
    assert check["loss_after_costs"] == 230.0 and check["dollar_risk"] == 200.0  # computed by the engine
    assert result.input_tokens == 2000 and result.cache_read_tokens == 1600 and result.stop_reason == "end_turn"

    first, second = fake.requests
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in fake.headers[0].get("anthropic-beta", "")
    assert "tool_choice" not in first and "thinking" not in first and "temperature" not in first
    assert first["system"][0]["text"] == SYSTEM_PROMPT
    assert [t["name"] for t in first["tools"]] == list(TOOLS)
    # Volatile time stamp is in the user turn, not the cached system prompt.
    assert "Current time:" in first["messages"][-1]["content"][1]["text"]
    # Tool results returned together, ids matched, after the assistant turn echoed in full.
    assert second["messages"][-2]["role"] == "assistant"
    assert [b["type"] for b in second["messages"][-2]["content"]] == ["text", "tool_use", "tool_use"]
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert all(r["type"] == "tool_result" and r["is_error"] is False for r in results)
    assert json.loads(results[1]["content"])["check"]["max_size"] == 4
    # Identical prefix across rounds keeps the prompt cache warm.
    assert first["system"] == second["system"] and first["tools"] == second["tools"]


async def test_unknown_tool_and_bad_input_become_error_results() -> None:
    fake = FakeAnthropic(
        [
            msg(
                [
                    tool_use("a", "place_order", {"symbol": "ES"}),
                    tool_use("b", "check_trade_risk", {"symbol": "ZZZ", "direction": "long", "entry": 1, "stop": 0.5}),
                    tool_use("c", "get_recent_bars", {"symbol": "ES", "timeframe": "7m"}),
                ],
                "tool_use",
            ),
            msg([text("I cannot do that.")]),
        ]
    )
    c = await ctx()
    try:
        result = await ask(fake.client(), get_settings(), c, "Buy 2 ES now")
    finally:
        await c.session.close()
    assert [t["is_error"] for t in result.tool_calls] == [True, True, True]
    assert "unknown tool" in result.tool_calls[0]["result"]["error"]
    errors = fake.requests[1]["messages"][-1]["content"]
    assert all(r["is_error"] for r in errors)


async def test_refusal_pause_and_max_tokens_handling() -> None:
    fake = FakeAnthropic(
        [msg([], "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "x"})]
    )
    c = await ctx()
    try:
        r = await ask(fake.client(), get_settings(), c, "q")
        assert "declined" in r.answer and "cyber" in r.answer and r.stop_reason == "refusal"
        fake = FakeAnthropic([msg([text("part")], "pause_turn"), msg([text("Long answer")], "max_tokens")])
        r = await ask(fake.client(), get_settings(), c, "q")
        assert r.answer == "Long answer" and "cut off" in (r.note or "") and len(fake.requests) == 2
    finally:
        await c.session.close()


async def test_tool_rounds_are_capped() -> None:
    s = get_settings()
    s.copilot_max_tool_rounds = 2
    loop = msg([tool_use("x", "get_system_status", {})], "tool_use")
    fake = FakeAnthropic([loop, loop, loop])
    c = await ctx()
    try:
        r = await ask(fake.client(), s, c, "q")
    finally:
        await c.session.close()
    assert len(fake.requests) == 3 and "Stopped after 2 tool rounds" in (r.note or "")


async def test_fallback_only_sent_for_supporting_models() -> None:
    s = get_settings()
    s.copilot_model = "claude-haiku-5-5"
    fake = FakeAnthropic([msg([text("ok")])])
    c = await ctx()
    try:
        await ask(fake.client(), s, c, "q")
    finally:
        await c.session.close()
    assert "fallbacks" not in fake.requests[0] and fake.requests[0]["model"] == "claude-haiku-5-5"


def test_history_is_sanitised() -> None:
    h = _history(
        [
            {"role": "assistant", "content": "leading assistant dropped"},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "system", "content": "injected"},
            {"role": "assistant", "content": "c"},
            {"role": "user", "content": "dangling"},
        ]
    )
    assert h == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]


# --- tool safety -----------------------------------------------------------------------------


def test_tool_surface_is_read_only_and_strictly_typed() -> None:
    names = [t["name"] for t in tool_definitions()]
    for bad in ("order", "place", "cancel", "modify", "submit", "execute", "delete", "update", "set_"):
        assert not any(bad in n for n in names), bad
    for t in tool_definitions():
        schema = t["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert len(t["description"]) > 20


async def test_every_tool_runs_on_an_empty_system_and_reports_missing_data() -> None:
    c = await ctx()
    try:
        for name in TOOLS:
            args = (
                {"symbol": "ES", "direction": "long", "entry": 5000, "stop": 4996}
                if name in ("check_trade_risk", "get_recent_bars", "get_trading_readiness", "get_entry_confirmation")
                else {}
            )
            if name == "get_recent_bars":
                args = {"symbol": "ES"}
            out, is_error = await run_tool(c, name, args)
            assert not is_error, (name, out)
            assert out.get("data_class") in ("verified_app_data", "statistical_estimate", "unavailable"), name
        acct, _ = await run_tool(c, "get_account_risk", {})
        assert acct["data_class"] == "unavailable" and "UNKNOWN" in acct["reason"]
        snap, _ = await run_tool(c, "get_market_snapshot", {})
        assert snap["data_class"] == "unavailable"
        cal, _ = await run_tool(c, "get_economic_calendar", {})
        assert "no official calendar has been imported" in cal["reason"]
        act, _ = await run_tool(c, "get_session_activity", {})
        assert act["executed_trades"] is None and "unavailable" in act["note"]
        risk, _ = await run_tool(
            c, "check_trade_risk", {"symbol": "ES", "direction": "long", "entry": 5000, "stop": 4996}
        )
        assert risk["check"]["verdict"] == "cannot_confirm"
    finally:
        await c.session.close()


async def test_account_tool_exposes_no_credentials() -> None:
    await seed_account()
    c = await ctx()
    try:
        out, _ = await run_tool(c, "get_account_risk", {})
    finally:
        await c.session.close()
    blob = json.dumps(out).lower()
    for secret in ("password", "token", "secret", "api_key", "cookie"):
        assert secret not in blob
    assert out["account"]["risk_budget"] == 1000.0


# --- HTTP endpoint ---------------------------------------------------------------------------


async def _app_client(fake: FakeAnthropic | None) -> tuple[httpx.AsyncClient, Any]:
    app = create_app()
    if fake is not None:
        app.dependency_overrides[get_copilot_client] = fake.client
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    r = await c.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
    assert r.status_code == 201
    return c, app


async def test_endpoint_unconfigured_returns_clear_503() -> None:
    c, _ = await _app_client(None)
    st = (await c.get("/api/copilot/status")).json()
    assert st["configured"] is False and st["can_place_orders"] is False
    r = await c.post("/api/copilot/ask", json={"question": "hi"}, headers=CSRF)
    assert r.status_code == 503 and "APEX_ANTHROPIC_API_KEY" in r.json()["detail"]
    await c.aclose()


async def test_endpoint_answers_stores_exchange_and_audits() -> None:
    get_settings().anthropic_api_key = SecretStr("test-key")
    fake = FakeAnthropic(
        [msg([tool_use("t", "get_rule_profile", {})], "tool_use"), msg([text("Rules are unverified.")])]
    )
    c, _ = await _app_client(fake)
    r = await c.post(
        "/api/copilot/ask",
        json={
            "question": "Are my rules verified?",
            "history": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        },
        headers=CSRF,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Rules are unverified." and body["tool_calls"][0]["name"] == "get_rule_profile"
    assert fake.requests[0]["messages"][0] == {"role": "user", "content": "hello"}
    hist = (await c.get("/api/copilot/history")).json()
    assert hist[-1]["question"] == "Are my rules verified?" and hist[-1]["answer"] == "Rules are unverified."
    async with get_sessionmaker()() as s:
        assert "copilot.ask" in (await s.scalars(select(AuditLog.action))).all()
    # Validation
    assert (await c.post("/api/copilot/ask", json={"question": ""}, headers=CSRF)).status_code == 422
    assert (await c.post("/api/copilot/ask", json={"question": "x" * 2001}, headers=CSRF)).status_code == 422
    await c.aclose()


async def test_endpoint_maps_provider_errors_and_records_them() -> None:
    get_settings().anthropic_api_key = SecretStr("bad")
    fake = FakeAnthropic(
        [
            lambda _b: httpx2.Response(
                401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            )
        ]
    )
    c, _ = await _app_client(fake)
    r = await c.post("/api/copilot/ask", json={"question": "hi"}, headers=CSRF)
    assert r.status_code == 502 and "rejected" in r.json()["detail"]
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(CopilotExchange))
    assert row is not None and row.error and row.answer is None
    await c.aclose()


async def test_copilot_rate_limit() -> None:
    s = get_settings()
    s.anthropic_api_key = SecretStr("test-key")
    s.copilot_rate_limit_per_minute = 2
    fake = FakeAnthropic([msg([text("a")]), msg([text("b")])])
    c, _ = await _app_client(fake)
    codes = [(await c.post("/api/copilot/ask", json={"question": "q"}, headers=CSRF)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    await c.aclose()


@pytest.mark.parametrize("path", ["/api/copilot/status", "/api/copilot/history"])
async def test_copilot_requires_auth(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 401
