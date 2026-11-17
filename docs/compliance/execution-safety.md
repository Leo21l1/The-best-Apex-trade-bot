# Execution safety policy

PROJECT APEX is an AI trading **assistant**, not an order-execution bot.

## Never implemented

- Automated Apex/Tradovate order entry, cancellation, or stop/target modification
- Browser-based order clicking, keyboard or mouse macros
- Order copying, undocumented account access, API permission bypasses

## Enforcement

| Control | Location |
|---|---|
| `ExecutionMode` enum has exactly one member, `ANALYSIS_ONLY`; the setting is a read-only property | `apps/api/app/core/config.py` |
| `LIVE_ORDER_ROUTING_ENABLED = False`; `forbid_live_execution()` raises unconditionally | `apps/api/app/core/execution_guard.py` |
| Test scans all API and web source for Tradovate order-mutating endpoints (`order/placeorder`, `order/placeoso`, `order/placeoco`, `order/modifyorder`, `order/cancelorder`, `order/liquidateposition`, `startorderstrategy`) and UI-automation libraries (pyautogui, pynput, selenium, playwright, keyboard, mouse) | `apps/api/tests/test_execution_safety.py` |
| `/api/system/status` reports `live_order_routing: false`; UI shows an `ANALYSIS ONLY` badge on every page | API + web shell |

## Paper trading

The Phase 6 simulator will be an in-process engine with no network client of any broker. It will
live in its own package, and the safety test will be extended to assert that it imports no HTTP or
WebSocket client.

## Read-only broker connectivity

Tradovate read-only access may be added only when officially documented, authorised for the
account, and technically verified. Until then: manual balance entry, position updates and CSV
trade import (Phase 4).

## Trading terminal (added later)

The Trading Terminal has BUY/SELL controls that submit to the **paper simulator only**. Its
"Live order (Tradovate)" button is permanently disabled and explains why
(`GET /api/execution/live-status` returns `live_trading_enabled: false`). The status is a constant
in code, not a setting.

Live trading stays disabled because no documentation was found showing that Apex and Tradovate
authorise third-party automated order entry for this evaluation account. An API connection alone
does not grant that permission. Re-enabling would need a separate, independently reviewed broker
adapter, and the code-scan tests here would fail the build if a Tradovate order endpoint appeared.

The manual workflow is:

1. Write a trade plan in the terminal.
2. Place the order yourself in Tradovate.
3. Mark the plan as executed manually.
4. Import the Tradovate CSV (or enter the trade) so it is journaled and reviewed.

The real-time trade assistant raises advisory alerts only; it never changes stops, targets or positions.
