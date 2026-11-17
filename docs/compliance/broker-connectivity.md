# Broker connectivity (Apex / Tradovate) — policy review

**Checked:** 2026-11-17. **Decision:** automatic read-only API synchronisation is **not** implemented.
A manual broker-statement import is used instead (Journal → Broker statement import).

## Findings

| Source | What it says | Effect |
|---|---|---|
| [Tradovate support: Tradovate API Access](https://support.tradovate.com/s/article/Tradovate-API-Access?language=en_US) | Prop firm and evaluation accounts are not eligible for API access. A live, funded Tradovate account (minimum $1,000) with the paid API Access add-on is required to create API keys. | An Apex evaluation account cannot obtain Tradovate API credentials. |
| [Apex: Prohibited Activities](https://apextraderfunding.com/help-center/getting-started/prohibited-activities/) | No automation or algorithm usage. | Rules out automated trading; says nothing that grants data access. |
| [Apex: PA / Legacy PA compliance](https://apextraderfunding.com/help-center/performance-accounts-pa/legacy-performance-account-pa-compliance/) | Automation is prohibited; traders are encouraged to submit software for approval before use. | Third-party software should be approved by Apex first. |
| [Apex: Tradovate API Request Limit](https://apextraderfunding.com/help-center/tradovate/tradovate-api-request-limit/) | Describes Tradovate's request limits only. | Not an authorisation to use the API. |

The pages above could not be fetched directly from the development environment (the Apex site
refuses automated clients and the Tradovate help centre was not reachable), so the wording was taken
from search-engine extracts of the official pages. Re-check them before relying on this decision,
and ask Apex support in writing if you want a definitive answer for your account.

## What is and is not built

- Not built: Tradovate login, credential or API-key storage, any API client, order submission,
  cancellation or modification, browser automation, scraping, unofficial endpoints.
- Built: import of Tradovate report exports you download yourself — Performance, Orders/Fills
  (paired FIFO into round trips), Cash History (daily realised P&L, commissions, fees, end-of-day
  balance), Positions (open positions and open P&L), end-of-day balances, or a generic trade layout.
  Several files can be uploaded together; the import previews first, skips duplicates (same report
  row, the same execution in another file, or a trade already stored), links trades to your trade
  plans, generates trade reviews, updates end-of-day balances for the risk engine, and reconciles
  journal P&L and fees with the cash history.

## What would be needed to enable automatic sync

1. Written confirmation from Apex that read-only API access to your account is permitted.
2. Tradovate API eligibility for that account (today evaluation accounts are excluded).
3. A separate, reviewed read-only adapter with credentials kept server-side; the code-scan tests in
   `apps/api/tests/test_execution_safety.py` would continue to forbid any order endpoint.
