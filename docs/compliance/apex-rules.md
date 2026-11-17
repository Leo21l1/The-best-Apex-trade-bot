# Apex rule profile — verification register

**Profile:** Apex Trader Funding · $50K · End-of-Day (EOD) Evaluation
**Status:** ⚠️ **UNVERIFIED** — no rule below has been checked against official Apex documentation.

While any rule is unverified the application must:

1. show an explicit warning (implemented: Overview callout, top-bar "Rules unverified" pill,
   System Health `apex_rule_profile = not_configured`), and
2. refuse to label any trade "compliant" (enforced by the Phase 4 risk engine).

## Provisional reference values (from the trader's configuration)

| Rule | Provisional value | Verified | Source URL | Verified on | Applies to | Version |
|---|---|---|---|---|---|---|
| Starting balance | $50,000 | No | — | — | — | — |
| Evaluation profit target | $3,000 | No | — | — | — | — |
| Maximum EOD drawdown | $2,000 | No | — | — | — | — |
| Daily loss limit | $1,000 | No | — | — | — | — |
| Maximum standard contracts | 6 | No | — | — | — | — |

## Still to verify (Phase 4)

- Applicable account generation / rule version
- EOD drawdown methodology (threshold set from end-of-day balance — **not** an intraday trailing
  drawdown; the two must never be confused)
- Daily loss methodology, daily reset and settlement times (America/New_York)
- Treatment of unrealised P&L
- Contract limits and micro-contract equivalents
- News trading restrictions
- Position close requirements
- Evaluation passing criteria (incl. minimum trading days or consistency rules, if any)
- Prohibited trading methods
- Automation and third-party software policies

Each verified rule will be stored in the database with its official source URL, verification date,
account applicability and version. Rules change; re-verification dates will be tracked.
