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
