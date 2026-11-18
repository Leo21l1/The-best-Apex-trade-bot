"""AUTHORIZED_BROKER gate and broker-state reconciliation.

Real-money automatic execution is allowed only when EVERY gate passes. There is deliberately no broker
adapter in this codebase yet (``ADAPTERS`` is empty), so the gate always refuses; adding one is a
separate, reviewed change. Apex Trader Funding accounts are refused unconditionally.

Reconciliation compares the local view (positions, working orders) with the broker's own report and
returns every mismatch; any mismatch must halt automated activity until resolved.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from app.core.config import Settings
from app.research.stats import bootstrap_mean_ci

FORBIDDEN_ACCOUNT_WORDS = ("apex", "evaluation", "funded", "prop", "pa account", "performance account")
MIN_FORWARD_TRADES = 30


class BrokerAdapter(Protocol):
    """What a broker integration must provide (API-key/OAuth authentication only - never passwords)."""

    async def account(self) -> dict[str, Any]: ...
    async def positions(self) -> dict[str, int]: ...
    async def open_orders(self) -> list[dict[str, Any]]: ...
    async def place_bracket(
        self, symbol: str, side: str, qty: int, stop: float, target: float, client_id: str
    ) -> dict[str, Any]: ...
    async def cancel(self, order_id: str) -> None: ...
    async def flatten(self, symbol: str) -> None: ...


#: name -> factory. Intentionally empty: no broker integration is implemented or enabled.
ADAPTERS: dict[str, Callable[[Settings], BrokerAdapter]] = {}


def forward_evidence_ok(forward_pnls: Sequence[float]) -> tuple[bool, str]:
    """Prospective (forward paper) evidence: >= 30 trades and a 95% expectancy interval above zero."""
    n = len(forward_pnls)
    if n < MIN_FORWARD_TRADES:
        return False, f"only {n} forward paper trades (need {MIN_FORWARD_TRADES})"
    ci = bootstrap_mean_ci(list(forward_pnls))
    if not ci or ci[0] <= 0:
        return False, f"forward paper expectancy interval {ci} is not above $0"
    return True, f"{n} forward paper trades, expectancy interval {ci}"


def broker_gate(settings: Settings, rules_profile: str, forward_pnls: Sequence[float]) -> list[str]:
    """Every reason real-money automatic execution is refused (empty list = all gates pass)."""
    reasons: list[str] = []
    if settings.automation_mode != "AUTHORIZED_BROKER":
        reasons.append(f"automation mode is {settings.automation_mode}, not AUTHORIZED_BROKER")
    acct = settings.broker_account_type.strip().lower()
    if not acct:
        reasons.append("no separately identified brokerage account type (APEX_BROKER_ACCOUNT_TYPE)")
    elif any(w in acct for w in FORBIDDEN_ACCOUNT_WORDS):
        reasons.append("Apex Trader Funding / prop-firm accounts may never be traded automatically")
    if rules_profile.lower().startswith("apex") and not acct:
        reasons.append("the configured rule profile is an Apex profile; automated execution is not permitted for it")
    if settings.broker_adapter not in ADAPTERS:
        reasons.append(
            f"no supported broker adapter is implemented or configured ({settings.broker_adapter or 'none'})"
        )
    ok, why = forward_evidence_ok(forward_pnls)
    if not ok:
        reasons.append(f"insufficient prospective evidence: {why}")
    if not settings.broker_live_enabled:
        reasons.append("separate manual live-enablement step not taken (APEX_BROKER_LIVE_ENABLED=false)")
    return reasons


def reconcile(
    local_positions: dict[str, int],
    broker_positions: dict[str, int],
    local_orders: Sequence[dict[str, Any]],
    broker_orders: Sequence[dict[str, Any]],
) -> list[str]:
    """Mismatches between our records and the broker's (signed quantities; orders by client id)."""
    issues = []
    for sym in sorted(set(local_positions) | set(broker_positions)):
        a, b = local_positions.get(sym, 0), broker_positions.get(sym, 0)
        if a != b:
            issues.append(f"{sym}: local position {a:+d}, broker position {b:+d}")
    ours = {o["client_id"]: o for o in local_orders}
    theirs = {o["client_id"]: o for o in broker_orders}
    for cid in sorted(set(ours) - set(theirs)):
        issues.append(f"order {cid} is working locally but unknown to the broker")
    for cid in sorted(set(theirs) - set(ours)):
        issues.append(f"broker order {cid} is not in our records (placed outside the system?)")
    for cid in sorted(set(ours) & set(theirs)):
        if int(ours[cid].get("qty", 0)) != int(theirs[cid].get("qty", 0)):
            issues.append(
                f"order {cid}: quantity {ours[cid].get('qty')} locally vs {theirs[cid].get('qty')} at the broker"
            )
    return issues
