# Phase 6 report — backtesting, paper trading, journal, analytics

Date: 2026-11-17

## 1. Completed features

- **Event-driven backtest engine** (`app/research/backtest.py`) that runs the same strategy code as
  the live scanner on stored 1-minute bars, evaluating only closed bars (a test proves adding future
  data never changes earlier trades).
  - Conservative fills: next-bar-open market entry plus slippage; targets need a one-tick
    trade-through; stop assumed first when both hit in one bar; gaps fill at the open; flat by
    15:55 ET; commission per side.
  - Metrics: win rate, average win/loss, profit factor, expectancy ($ and R), t-statistic, net
    after costs, commission, max drawdown ($ and R), trades per day, exposure, daily Sharpe, exit
    reasons.
  - In-sample / out-of-sample split (last 30% of days), walk-forward folds, seeded Monte Carlo
    bootstrap, parameter sensitivity (target and slippage variants), multiple-testing count with a
    Bonferroni threshold.
  - Validation against the research standards; simulated data can never validate.
  - Every experiment stores parameters, dataset fingerprint (SHA-256 of the bars), engine version
    and a result hash; re-running gives an identical hash.
  - A precomputed replay path keeps runs to about 2–3 seconds per strategy per month of 1-minute
    data, and a test checks it matches the live level calculation exactly.
- **Strategy validation is now evidence-driven**: the latest completed experiment on real historical
  data decides whether a strategy is validated.
- **Ranking at request time**: a candidate is ranked only with a validated strategy, live or
  delayed data, a verified rule profile, a passing one-contract risk check and no active event
  window; ranked by validated expectancy, then setup score.
- **Backtest queue** run by the worker (claimed with row locks, stuck runs recovered on restart).
- **Paper-trading simulator** (`app/paper/engine.py`): market, limit, stop, bracket stop-loss and
  take-profit (OCO), partial fills capped by bar volume, slippage, commissions, rejections (unknown
  symbol, bad tick, contract limit counting micros as 0.1, wrong-side bracket, market closed, halted
  account), position accounting including shorts and reversals, EOD drawdown threshold from
  end-of-day balances, daily loss halt until the next trading day, permanent breach, end-of-session
  flatten at 16:58 ET, duplicate-bar protection. No network code; a safety test enforces this.
  Closed paper trades flow into the journal automatically.
- **Trading journal**: CSV import of Tradovate Performance exports (contract codes mapped to roots,
  shorts detected from timestamps, fill-id dedupe) and a generic layout; estimated fees when absent
  (labelled); all-or-nothing import with dry run; per-trade strategy tag, planned stop/target,
  followed-plan flag, mistake tags and notes.
- **Performance analytics**: daily / weekly / monthly reports; breakdowns by strategy, instrument,
  entry hour and direction; recurring-mistake analysis with Welch's t-test and small-sample flags;
  planned-vs-actual; rule checks against configured values (contract limit, held into the close,
  open during a high-importance event window, realised daily loss).
- **Dashboard**: Backtesting (experiment form and history, metrics, equity curve, validation
  checks, IS/OOS and folds, Monte Carlo, sensitivity, trades, assumptions), Paper Trading (new page:
  order ticket, positions, working orders, history, activity, flatten, reset), Trading Journal
  (import, filter, edit) and Performance Analytics.
- **Fixed during testing**: flattening a paper position now writes the round trip to the journal.

## 3–4. Tests executed and results

| Suite | Result |
|---|---|
| API lint and format | pass |
| API tests | **266 passed** |
| Web lint, typecheck, build | pass |
| Web unit tests | **35 passed** |
| End-to-end | **21 passed** |

## 5. Known limitations

- **All backtests so far ran on simulated data**, so no strategy is validated and the ranked list
  stays empty. Real validation needs a licensed historical data provider.
- The backtester holds one position at a time with a fixed quantity and exits everything at the
  first target. No contract-roll, spread or queue-position modelling beyond slippage; holidays are
  not modelled.
- The first indicator bucket of each 120-hour replay window may differ slightly from the live
  scanner's (documented in each result's assumptions).
- Parameter sensitivity covers target multiples and slippage only; strategy thresholds themselves
  are fixed by design (no automatic optimisation).
- The Tradovate Performance column layout is matched by header names known from commonly shared
  exports; it has not been tested against a file exported from your account.
- Paper market orders fill only after the next 1-minute bar is stored (up to about a minute).
- Rule checks on imported trades see realised P&L only; intraday unrealised drawdown is not in
  broker exports.

## 8. Next phase (7 — hardening)

Security review, failure and disconnect testing, performance, responsive design, deployment docs.
