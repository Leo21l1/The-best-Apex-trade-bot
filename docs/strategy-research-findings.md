# Strategy research findings and open hypotheses (engine setups-1.1)

Sources: the user's local confluence studies (`research_out/confluence_study_ES.md` / `_NQ.md`, development data
2025-12-01 .. 2026-09-08, 198 sessions, holdout from 2026-09-09 excluded) and a code review of the setup engine.
These reports live only on the user's PC; numbers below are the ones the user reported.

## What the audited results say

| View: risk-eligible, one position at a time | ES 3/5 | NQ 3/5 | ES 4/5 | NQ 4/5 |
|---|---|---|---|---|
| Resolved opportunities | 32 | 23 | 1 | 2 |
| Win rate | 25.0% | 30.4% | 0/1 | 1/2 |
| $/trade (95% CI) | -59.30 (-301.88 .. +224.33) | +436.52 (-325.94 .. +1,521.47) | loss | +1,227.50 |
| Profit factor | 0.81 | 2.06 | - | - |
| Max drawdown | $4,797.50 | $5,540 | - | - |
| Evidence | negative / inconclusive | inconclusive, few large winners | insufficient | insufficient |

No configuration shows statistically reliable positive net expectancy. Both 3/5 drawdowns exceed the $2,000
trailing-drawdown profile: with account termination modelled (new `account_survival`), a single evaluation
account would have been stopped; the study now reports when.

## Why so few setups qualify (code-level causes, verified on the engine)

1. **TP1 is the nearest opposing level, the stop the nearest qualifying structural level.** The most common
   TP1 basis is the nearest opposing FVG/order block (often a small 5m zone); the stop is typically a 5m/15m
   pivot beyond the entry zone. Median stop distance is about 2x the TP1 distance (simulated check: 1.9 vs
   0.7 x ATR5m), so net R:R >= 1.5 to TP1 is rare. Costs are not the main cause (few plans fail only after
   costs); TP2 often would clear the gate. This is the design, not a calculation bug.
2. **Higher-timeframe bias is mostly NEUTRAL, not mostly "bad location".** Bias needs 15m swing structure
   (50-bar pivots). In the analyzer's 8-day window a 15m swing break is rarely confirmed, so 15m stays neutral
   and so does the bias; the 1h side already falls back to internal structure, the 15m side does not.
3. **Location contradicts continuation/breakout.** In an uptrend price usually sits in the premium half of the
   15m range, so factor 1 fails trend longs even when the bias is right.
4. **Factor 2 (structure) passes ~80%:** a recent 5m internal BOS/CHoCH almost always exists; it discriminates
   little. Factor 5 (momentum) ~46%.
5. **Strategy-native exits are replaced.** The study and the live analyzer use structural levels, not the exits
   each strategy was designed with. The earlier "ES MTF Trend T1" hypothesis exits at 1.5R gross, which can
   never clear a 1.5 *net* R:R gate after costs - it cannot produce a confirmed setup under the live rules.

## Pre-registered experimental hypotheses (evaluated by `confluence-study`, never used live)

| ID | Change | Rationale |
|---|---|---|
| H1 | Premium/discount location required only for reversal and SMC families | cause 3 |
| H2 | Continuation/breakout TP1 from major levels only (PDH/PDL, weekly/monthly, overnight, prior RTH, 15m swing) | cause 1 |
| H3 | 15m internal-structure fallback for the bias when the 15m swing is unconfirmed (mirrors the 1h rule) | cause 2 |
| H4 | Strategy's own stop with its second designed target as the exit (the earlier "T2" research) | cause 5 |
| H1+H2+H3 | combined | interaction |

All variants use the same signals, analyzer documents, outcome rules, costs, deduplication, one position at a
time, the planned-risk cap and account survival. Six variants x three thresholds are compared: a single
favourable cell is expected by chance; nothing is promoted from development data. A hypothesis earns live
consideration only if it is (a) positive with an interval above $0 on development data, (b) survives the
account rules, (c) is not dominated by one or two winners, and then (d) holds on genuinely new prospective
signals recorded with frozen settings.

## How to run (stored data only, outside market hours)

```
python -m app.cli confluence-study --symbol ES --days 365 --holdout-sessions 50
python -m app.cli confluence-study --symbol NQ --days 365 --holdout-sessions 50
python -m app.cli confluence-study --symbol MNQ --days 365 --holdout-sessions 50
python -m app.cli confluence-combine research_out/confluence_study_ES.json research_out/confluence_study_NQ.json
```

MNQ: runs only if enough MNQ bars are stored; it reports an error otherwise. NQ results never stand in for
MNQ. MNQ commission is about 2.4x NQ in points ($1.20 round trip = 0.6 pt vs $5 = 0.25 pt; slippage is 1 tick on both),
so small-target setups are less viable on MNQ; dollar risk is 1/10 of NQ for the same structure.
