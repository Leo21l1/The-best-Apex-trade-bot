# Automation: scanner, autonomous paper trading, phone alerts and the real-money gate

Nothing in PROJECT APEX can place, change, cancel or close an order on an Apex Trader Funding account,
and no broker integration is installed. Real-money automatic execution is refused by design until the
separate gates below pass - today they cannot.

## Modes (`APEX_AUTOMATION_MODE` in `.env`)

| Mode | What runs | Orders |
|---|---|---|
| `APEX_MANUAL` (default) | Live ES/NQ scanner (all 11 strategies), entry confirmation, alerts and phone notifications | None. You place every order yourself. |
| `AUTONOMOUS_PAPER` | The above + the autopilot trading a separate simulated account named "Autonomous paper" | Simulated only, labelled `[SIMULATED]` |
| `AUTHORIZED_BROKER` | The real-money gate is evaluated; it refuses and alerts | None (no broker adapter exists) |

## Autopilot rules (AUTONOMOUS_PAPER)

* Trades only pre-registered setups: `APEX_AUTOPILOT_STRATEGIES`, e.g. `ES:mtf_trend:t1_all` and
  `NQ:breakout_retest:t2_all` (instrument : strategy : research configuration). The configuration's own
  rules apply exactly as in research (exit at T1 or T2, its net R:R gate if any), so forward results are
  comparable, new, prospective evidence - the previously inspected holdout is not reused.
* Each signal is evaluated once (no duplicate orders). Entry is a simulated market order with a
  protective stop and a target (bracket, one cancels the other); fills follow the paper engine's
  conservative rules (next bar open + slippage, targets need a trade-through, partial fills by volume).
* No new entry when: the kill switch is on; the live feed is not streaming LIVE; the instrument's data is
  stale (> 3 minutes while the market is open) or the market is closed; the simulated account hit its
  daily loss or drawdown limit; a high-impact news window applies or no calendar is loaded; a position
  or working entry already exists in the same correlation group (ES/NQ/MES/MNQ count together); the
  1-contract loss at the stop (incl. costs) exceeds `APEX_AUTOPILOT_MAX_RISK_PER_TRADE` or the
  remaining daily loss allowance.
* Fixed size (`APEX_AUTOPILOT_CONTRACTS`, max 2). No martingale, no size increase after losses.
* Emergency stop: the button on the Paper Simulator page, `POST /api/autopilot/kill`, or
  `python -m app.cli autopilot kill`. The next cycle (<= 15 s) cancels working simulated orders and
  flattens simulated positions. Resume with `autopilot resume`.
* The autonomous account is separate from your manual paper account and is not copied into your journal.
  Its forward results are on the Paper Simulator page (Autopilot panel), `GET /api/autopilot` and
  `python -m app.cli autopilot status`.

## Real-money gate (AUTHORIZED_BROKER) - every item must pass

1. Mode is `AUTHORIZED_BROKER`.
2. `APEX_BROKER_ACCOUNT_TYPE` names a separate brokerage account that permits algorithmic trading. Any
   Apex / evaluation / funded / prop account is refused unconditionally.
3. A supported broker adapter is installed (API-key/OAuth authentication only, never passwords or 2FA
   codes). **None exists in this codebase**; adding one is a separate, reviewed change that must also
   implement order/position reconciliation (`app/execution/broker.py: reconcile`) before every action
   and after every reconnect.
4. Prospective evidence: >= 30 closed autonomous paper trades with a 95% expectancy interval above $0.
5. A separate manual live-enablement step: `APEX_BROKER_LIVE_ENABLED=true`.

While any item fails, no order of any kind is created and a `Real-money automation REFUSED` alert lists why.

## Phone notifications

Choose one provider in `.env` (secrets stay in `.env`, never in Git):

* **ntfy** (simplest): install the ntfy app, subscribe to a long random topic, set
  `APEX_NOTIFY_PROVIDER=ntfy` and `APEX_NOTIFY_NTFY_URL=https://ntfy.sh/<your-long-random-topic>`
  (anyone who knows the topic can read it: make it long and random, or use your own ntfy server/token).
* **Pushover**: `APEX_NOTIFY_PROVIDER=pushover`, `APEX_NOTIFY_PUSHOVER_TOKEN`, `APEX_NOTIFY_PUSHOVER_USER`.
* **Telegram**: `APEX_NOTIFY_PROVIDER=telegram`, `APEX_NOTIFY_TELEGRAM_BOT_TOKEN`, `APEX_NOTIFY_TELEGRAM_CHAT_ID`.

Every app alert is pushed (minimum severity `APEX_NOTIFY_MIN_SEVERITY`): new setups, setup invalidation,
stale or disconnected data, risk thresholds, account snapshot stale, economic events, and in
AUTONOMOUS_PAPER mode every `[SIMULATED]` order, fill, close, skipped signal, pause and kill switch.
Test with `python -m app.cli autopilot notify-test`.
