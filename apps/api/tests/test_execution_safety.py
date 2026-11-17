"""Compliance tests: PROJECT APEX must contain no live order-routing path."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import ExecutionMode, Settings
from app.core.execution_guard import (
    LIVE_ORDER_ROUTING_ENABLED,
    LiveExecutionForbiddenError,
    forbid_live_execution,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = Path(__file__).resolve().parents[3]

# Tradovate order-mutating REST endpoints and UI-automation libraries.
FORBIDDEN_PATTERNS = [
    r"order/placeorder",
    r"order/placeoso",
    r"order/placeoco",
    r"order/modifyorder",
    r"order/cancelorder",
    r"order/liquidateposition",
    r"startorderstrategy",
    r"\bimport\s+pyautogui",
    r"\bimport\s+pynput",
    r"\bfrom\s+selenium",
    r"\bimport\s+selenium",
    r"\bfrom\s+playwright",
    r"\bimport\s+keyboard\b",
    r"\bimport\s+mouse\b",
]


def _source_files() -> list[Path]:
    files = list(APP_DIR.rglob("*.py"))
    web = REPO_ROOT / "apps" / "web"
    for sub in ("app", "components", "features", "hooks", "lib"):
        d = web / sub
        if d.exists():
            files += [p for p in d.rglob("*") if p.suffix in {".ts", ".tsx", ".js"}]
    return files


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_no_live_order_routing_code(pattern: str) -> None:
    rx = re.compile(pattern, re.IGNORECASE)
    hits = [str(p) for p in _source_files() if rx.search(p.read_text(errors="ignore"))]
    assert hits == [], f"Forbidden live-execution pattern {pattern!r} found in {hits}"


def test_guard_constants() -> None:
    assert LIVE_ORDER_ROUTING_ENABLED is False
    with pytest.raises(LiveExecutionForbiddenError):
        forbid_live_execution("place order")


def test_execution_mode_cannot_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEX_EXECUTION_MODE", "LIVE")
    s = Settings()
    assert s.execution_mode == ExecutionMode.ANALYSIS_ONLY
    assert [m.value for m in ExecutionMode] == ["ANALYSIS_ONLY"]
