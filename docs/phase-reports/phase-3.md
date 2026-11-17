# Phase 3 report — strategies, scanner, opportunities, alerts

Date: 2026-11-17

## 1. Completed features

- **Contract specifications** for all 12 instruments: tick size, tick value, point value, micro
  ratio (`app/marketdata/instruments.py`).
- **Indicators** as pure functions: SMA, EMA, Wilder ATR and RSI, Bollinger bands, session VWAP
  with standard deviation (anchored 09:30 ET), prior-day high/low/close, overnight high/low,
  opening range (09:30–09:45 ET). Values with too little history are `None`, never guessed.
- **Timeframe aggregation in Python** that matches the SQL aggregation bar for bar (tested on all
  nine timeframes), so the same logic can run live and in replay.
- **Strategies A–H** (`app/strategies/library.py`), each with a written specification (required
  data, instruments, regime, entry, invalidation, stop, targets, time exit, testing method, cost
  assumptions) and mathematically defined rules on closed 5-minute bars. Every evaluation returns
  each condition with the measured values.
- **Scanner** in the worker: every 20 seconds it builds the radar for every symbol and all nine
  timeframes, evaluates all strategies, stores new signals once, advances signal lifecycle
  (active, invalidated, target reached, expired) and caches the scan for the API. It refuses to
  scan when the data feed is not reporting.
- **Opportunity records**: entry zone, stop, targets, reward:risk, dollar risk per contract,
  assumed cost, loss at stop after costs, itemised setup score, validation status, uncertainty
  notes, news exposure and risk eligibility placeholders.
- **Ranking rule**: an opportunity is ranked only if its strategy is validated, the data is live
  or delayed, and it passed risk checks. No strategy is validated yet, so the ranked list is empty
  by design; triggered setups appear as unranked research candidates with the reasons listed.
- **Alerts**: opportunity detected, setup invalidated, regime changed, data feed problem. Dedupe
  keys with cooldowns, stored history, read state, WebSocket push.
- **Dashboard**: Market Radar (filter, watchlist, per-symbol levels and strategy checks),
  Opportunity Leaderboard (filters, explanation of each candidate), Strategy Laboratory, alerts
  bell.

## 3–4. Tests executed and results

| Suite | Result |
|---|---|
| API lint and format | pass |
| API tests | **137 passed** |
| Web lint, typecheck, build | pass |
| Web unit tests | **29 passed** |
| End-to-end | **11 passed** |

New coverage: tick value and point value for all 12 contracts, dollar-move arithmetic, indicator
known values, VWAP session reset, level calculation without look-ahead, Python/SQL aggregation
parity, strategy specification completeness, signal well-formedness over two simulated sessions
(stop and targets on the correct side, tick-aligned, triggered by a closed bar, reproducible),
crafted opening-range-breakout and liquidity-sweep tapes, cost and score arithmetic, signal
outcome rules, scanner idempotence, alert cooldowns, API and WebSocket behaviour.

## 5. Known limitations

- **No strategy has been backtested.** The rules are definitions, not evidence of an edge.
- Strategies E, F and G were exercised on simulated data and by property tests, not by
  hand-crafted scenarios; strategies C and A have crafted scenarios.
- The setup score is an uncalibrated checklist, not a probability.
- Cost figures are assumptions ($2.50 per side standard, $0.60 micro, one tick slippage per
  side), configurable, not broker quotes.
- Daily and weekly radar cells need more history than the default 72-hour backfill and show
  "insufficient history" until enough bars are stored.
- VWAP is anchored at 09:30 ET for all products, including crude oil and gold whose pit sessions
  open earlier. The opening-range strategy is limited to equity index futures for that reason.
- "Entry condition reached" alerts are not implemented (signals trigger at bar close).
- Fair value gaps and swing-structure detection are not implemented yet.
- All of this has only run on simulated prices.

## 8. Next phase (4 — Apex rules and risk)

Apex rule profile with per-rule verification records, deterministic risk engine (position sizing,
EOD drawdown, daily loss limit), account snapshots by manual entry and CSV import, Account Health
and Risk Guardian pages.
