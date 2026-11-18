# Strategy validation plan (proposal)

Status: proposal. No strategy is validated. Nothing here changes the conservative defaults
(net R:R to T1 >= 1.5, Risk Guardian limits). Live order execution stays disabled.

## Why the current sample cannot decide anything

* 24 usable regular-hours sessions per instrument (Oct-Nov 2026).
* The existing standards (`app/research/validation.py`) require, per strategy and configuration:
  real historical data, >= 100 trades in total, >= 30 out-of-sample trades, out-of-sample profit
  factor >= 1.20, out-of-sample expectancy >= 0.05 R, max drawdown <= 15 R, profitable in >= 60% of
  walk-forward folds, with a Bonferroni correction for the number of configurations compared.
* At the signal rates observed (roughly 0-1 trade per strategy per session), 30 out-of-sample trades
  need about 100 out-of-sample sessions, i.e. several hundred sessions in total.

## Data

1. Backfill **ES and NQ to 250 regular-hours sessions** (about one year). Estimate first:
   `python -m app.cli backfill --symbols ES,NQ --sessions 250` (estimate only, not billed), then approve
   explicitly with `--approve-cost`. Optionally 120 sessions first, then extend.
2. `python -m app.cli data-audit --symbols ES,NQ --days 400` must show no integrity errors and an
   explanation for every incomplete session before any study is run.
3. Contract rolls: continuous front-month prices (`ROOT.v.0`) are not back-adjusted, so each quarterly
   roll (ES/NQ: about 8 days before the third-Friday expiry in Mar/Jun/Sep/Dec) is a price jump.
   Strategies are flat by 15:55 ET, so no position is held across a roll, but indicators and the
   previous-day levels on the first session after a roll mix two contracts. **Exclude the roll
   session and the following session** from entries (to be implemented before the longer study;
   `data-audit` lists the roll dates in range).

## Method: anchored walk-forward, no optimisation

* Strategy rules and the five pre-registered R:R configurations stay **fixed**: no parameter is fitted
  on the data, so every out-of-sample session is genuinely unseen.
* Chronological folds by regular-hours session: first 100 sessions as the initial in-sample window,
  then test blocks of 25 sessions, re-anchored after each block (about 6 out-of-sample blocks on 250
  sessions). Report each block separately and pooled.
* Costs as configured: $2.50 commission per side per standard contract (verify against your Apex /
  Tradovate statement), 1 tick slippage on market entries, stops and time exits, none on limit
  targets. Repeat with 2 ticks as a stress test; a result that only holds at 1 tick is not robust.
* Multiple comparisons: 5 configurations x N strategies per instrument; keep the Bonferroni
  correction, and treat ES and NQ as separate tests (they are highly correlated, so agreement between
  them is weak extra evidence, not independent confirmation).

## Apex evaluation constraints (simulate the account, not just the trades)

Using the configured rule profile (`apex-50k-eod-evaluation`; values are UNVERIFIED until checked
against apextraderfunding.com and marked verified in the app):

* $50,000 start, $3,000 profit target, $2,000 end-of-day trailing drawdown, $1,000 daily loss limit,
  max 6 standard contracts, flat before 16:59 ET.
* Replay the out-of-sample trades in order, 1 contract, through the Risk Guardian rules: stop the
  account at a drawdown breach, stop the day at the daily loss limit.
* Report: probability of reaching the target before a breach across all rolling 20-trading-day
  windows, median days to target, breach rate, worst daily loss. A strategy with positive expectancy
  can still fail the evaluation through path risk; that is the number that matters for Apex.

## Decision rule

A configuration may be proposed as the default only if, out of sample: all validation checks pass
(after the multiple-comparison correction), it is positive in >= 60% of walk-forward blocks, it
survives the 2-tick stress test, and the Apex replay shows a breach rate the user accepts. Otherwise
the conservative default stays and the strategy remains "research only".
