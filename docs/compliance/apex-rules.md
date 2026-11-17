# Apex rule profile — verification register

**Profile:** Apex Trader Funding · $50K · End-of-Day (EOD) Evaluation (`apex-50k-eod-evaluation`)
**Status:** ⚠️ **UNVERIFIED.** No rule has a verification record.

## How verification works in the app

The rule profile lives in the database (`rules` table), not in code. On **Apex Account Health →
Rule profile**, an administrator can:

1. open the suggested official page for each rule,
2. correct the value if it differs,
3. enter the official source URL (must be `https://` on `apextraderfunding.com`), what the rule
   applies to, and the page version/date, and tick the confirmation box.

Each action is audit-logged. Changing a value clears its verification. The profile counts as
verified only when **all 15 required rules** are verified. Until then:

- the Overview, Account Health and top bar show "Rules unverified";
- System Health reports `apex_rule_profile` as degraded with the count;
- the Risk Guardian never sets `may_be_called_compliant`, and its summary says the profile is
  unverified;
- no opportunity can be ranked.

PROJECT APEX does not fetch, scrape or interpret Apex's pages itself. A human confirms each value.

## Research notes (2026-11-17) — not a verification

I could not open Apex's pages from the build environment (the host is blocked), so the notes below
come from web-search snippets of the official help centre plus third-party summaries. Treat them as
leads to check, not facts.

| Rule | Snippet / finding | Source (as indexed) |
|---|---|---|
| Profit target, drawdown, daily loss, contracts | 50K EOD Evaluation: $3,000 target, $2,000 max EOD drawdown, $1,000 daily loss limit, 6 contracts (60 micros). Matches the provisional values. | [EOD Evaluations](https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/) |
| Drawdown method | "No intraday trailing drawdown"; drawdown "calculated once daily at market close and enforced following trading session". | [EOD Evaluations](https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/), [EOD Drawdown Explained](https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-drawdown-explained/) |
| Enforcement timing | **Conflicting.** One third-party page quotes Apex as saying the threshold is "enforced in real time" even though it is calculated at end of day; another summary says touching the threshold at any point liquidates positions. | [H2T Funding](https://h2tfunding.com/end-of-day-drawdown/) |
| Daily loss limit | Reportedly pauses trading for the session rather than failing the evaluation. | EOD Evaluations (search snippet) |
| Time limit | Reportedly 30 calendar days to reach the target; no minimum trading days. | EOD Evaluations (search snippet) |
| Trailing stop level | Older (legacy) pages say the threshold stops trailing at starting balance + $100 / profit-target level; unclear for current EOD accounts. | [Legacy Evaluation Rules](https://apextraderfunding.com/help-center/evaluation-accounts-ea/legacy-evaluation-rules/) |
| Rule version | Third parties mention a rules rewrite effective 2026-03-01 ("Apex 4.0"). | NexusFi / Proptradingvibes (third-party) |
| Close time | Third parties give 4:59 PM ET for the daily recalculation. | third-party |

## Assumptions the risk engine makes while unverified

These are deliberately conservative. Each is stored as a text rule so you can correct and verify it.

- **Threshold** = max(starting balance, highest end-of-day balance) − $2,000. Intraday peaks never
  move it. This is EOD drawdown, not intraday trailing drawdown.
- **Enforcement**: treated as real-time against **equity** (balance + unrealised P&L), the stricter
  of the two readings above.
- **Daily loss** measured from the start-of-day balance (the latest EOD balance before today),
  including unrealised P&L. A profitable day does not raise the allowance.
- **Micros** count as 0.1 standard contract toward the 6-contract limit.
- **Trailing stop level**: not applied unless you configure `drawdown_stop_level`.
- **Trading day** ends 17:00 ET; a new day starts 18:00 ET.

## Still to verify

All 15 required rules, including: account generation/version, drawdown and daily-loss
methodology, reset and settlement times, unrealised P&L treatment, micro equivalence, news
restrictions, position-close requirements, passing criteria, prohibited methods, and the
automation/third-party software policy.
