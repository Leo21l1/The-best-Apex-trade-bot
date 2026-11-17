# Phase 4 report — Apex rules, risk engine, account data

Date: 2026-11-17

## 1. Completed features

- **Rule profile in the database** with a verification record per rule (value, official source
  URL, verification date, verifier, applies-to, version, notes). 5 numeric rules drive the engine;
  10 text rules document methodology; 1 optional rule (trailing stop level). Seeded unverified with
  the provisional values. Admin-only edit / verify / unverify, all audit-logged. Verification only
  accepts `https://` sources on `apextraderfunding.com`; changing a value clears verification.
- **Risk engine** (`app/risk/engine.py`), pure Decimal arithmetic, no AI:
  - EOD drawdown threshold from end-of-day balances only, with optional stop level;
  - equity-based real-time enforcement (conservative), drawdown room;
  - daily loss from the start-of-day balance, remaining allowance, combined risk budget;
  - profit-to-target on closed balance;
  - staleness measured against the last moment the market was open;
  - contract exposure with micros as 0.1, correlation groups (equity index, crude, gold);
  - trade check with the six required outputs: dollar risk, maximum size (by risk budget and by
    contract limit), reward:risk, loss after assumed costs, share of budget consumed, violations;
  - verdicts `violation` / `cannot_confirm` / `no_violation_found`, and a separate
    `may_be_called_compliant` flag that is true only with a verified profile and fresh data.
- **Account data** with no broker connection: manual snapshots (balance, unrealised P&L,
  end-of-day flag, positions with stops) and CSV import of end-of-day balances (common header
  names, preview, all-or-nothing), snapshot history and admin delete.
- **Integration**: opportunity candidates get a live one-contract risk check; System Health shows
  rule verification status; the scanner raises `account_stale` and `risk_threshold` alerts.
- **Dashboard**: Apex Account Health (state, threshold-to-target meter, snapshot form, CSV import,
  history, rule table with verification workflow) and Risk Guardian (trade form, six outputs,
  violations, warnings). Overview now shows the real risk budget and rule values.
- **Bug fixed during testing**: per-IP rate limiting throttled the whole dashboard because every
  request through the Next.js proxy arrives from 127.0.0.1. Signed-in requests are now limited per
  user, and `X-Forwarded-For` is trusted only from configured proxy addresses.

## 3–4. Tests executed and results

| Suite | Result |
|---|---|
| API lint and format | pass |
| API tests | **194 passed** |
| Web lint, typecheck, build | pass |
| Web unit tests | **32 passed** |
| End-to-end | **15 passed** |

Risk coverage (all hand-computed): initial threshold, EOD-only trailing, losing days never lower
it, an intraday peak does not move it, stop-level cap, start-of-day balance, account figures,
budget limited by drawdown vs daily loss, profitable days don't raise the allowance, breach and
limit flags, target on closed balance, unknown account, staleness across weekend / break / new
session, micro exposure, per-contract cost and loss for ES/MES/CL/NQ, flooring of max size,
compliance-label gating, daily-loss and drawdown violations, contract limit with mixed micros,
correlated exposure, short-trade arithmetic, input validation. API coverage: verification source
rules, value change clearing verification, profile verified only when all required rules are,
viewer permissions, snapshot validation (10 cases), CSV layouts and error reporting, risk endpoint,
account alerts with dedupe, IP and per-user rate limiting.

## 5. Known limitations

- **No rule is verified.** Apex's site is blocked from the build environment; the rule values
  match search snippets of the official help centre but nobody has checked the pages themselves.
- **Enforcement timing is assumed real-time** against equity (conservative) because sources
  disagree.
- **Account data is only as current as what you enter.** There is no broker connection.
- The CSV importer reads end-of-day balances only; it has not been tested against an actual
  Tradovate export, whose column names may differ (unrecognised headers are reported, not guessed).
- Exchange holidays are not modelled in staleness or trading-day calculations.
- Correlation groups are fixed categories, not measured correlations.
- Cost figures are assumptions, not broker quotes.

## 6. Required credentials

None.

## 8. Next phase (5 — AI copilot, economic calendar, briefings)
