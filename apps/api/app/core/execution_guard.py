"""Execution-safety boundary.

PROJECT APEX is analysis-only. This module is the single place that states the policy and is
enforced by tests (``tests/test_execution_safety.py``) which scan the codebase for any live
order-routing path. Paper trading (Phase 6) is a separate in-process simulator with no network
access to any broker.
"""

from __future__ import annotations

from typing import NoReturn

LIVE_ORDER_ROUTING_ENABLED = False

POLICY = (
    "PROJECT APEX never submits, cancels or modifies live orders. All orders are placed manually "
    "by the trader in their authorised Tradovate interface."
)


class LiveExecutionForbiddenError(RuntimeError):
    pass


def forbid_live_execution(action: str) -> NoReturn:
    """Raise unconditionally. Any code path that would touch a live order must call this."""
    raise LiveExecutionForbiddenError(f"{action!r} refused: {POLICY}")
