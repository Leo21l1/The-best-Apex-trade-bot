# Integration status

Last reviewed: 2026-11-17

| Integration | Status | Notes |
|---|---|---|
| Market data: `simulated` | Working | Synthetic prices for development. Labelled SIMULATED everywhere. Never use for trading. |
| Market data: `databento` | Adapter written, **unverified** | Historical 1-minute bars only. Not run against the live service (no API key; the build environment also blocks the host). |
| Real-time market data | Not implemented | Needs a licensed streaming adapter (see below). |
| Tradovate market data / read-only | Not used | See research below. |
| Economic calendar | Not connected | Phase 5. Requires an authorised source. |
| Claude API (copilot) | Not configured | Phase 5. Key stays on the API server. |

## Tradovate data access: research findings

**None of this comes from an official Tradovate, CME or Apex policy page.** The findings below are
from Tradovate community forum threads and third-party vendor blogs found by web search, so treat
them as unconfirmed and check with Tradovate and Apex support before relying on them.

- Real-time market data over the Tradovate API reportedly requires the account holder to register
  with CME as a sub-vendor (a CME licence fee quoted between roughly $290 and $500 per month,
  depending on the post and year), in addition to a paid API access add-on.
  Sources: [Tradovate forum: sub-vendor requirement](https://community.tradovate.com/t/is-cme-sub-vendor-requirement-for-api-access-is-290-per-month/6215),
  [Tradovate forum: market subscriptions and API](https://community.tradovate.com/t/whats-the-deal-with-market-subscriptions-and-api-access-to-charts-do-we-need-another-payment/11039),
  [Tradovate forum: CME vendor registration](https://community.tradovate.com/t/how-to-do-the-cme-vendor-registration-for-market-data-access-via-websocket/11320),
  [PickMyTrade blog (vendor with a commercial interest)](https://blog.pickmytrade.io/cme-ila-fee-explained-why-it-costs-290-427-mo/).
- Whether an Apex-provisioned Tradovate evaluation account can enable API access at all was not
  established. There is a forum thread asking exactly this
  ([Tradovate forum: API for prop firm accounts](https://community.tradovate.com/t/how-can-i-use-tradovate-apis-for-prop-firm-eval-and-paid-accounts/7814));
  it has not been read in full and no official answer was found.
- Apex's help centre has a page on a Tradovate API request limit
  ([Apex help centre](https://apextraderfunding.com/help-center/tradovate/tradovate-api-request-limit/)),
  which suggests some API use exists, but it does not state what is permitted.
- Third-party sources disagree about Apex's automation policy (some say semi-automated only,
  others say bots are allowed). PROJECT APEX places no orders, so it does not depend on the
  answer, but the official policy still needs to be read in Phase 4.

**Decision:** do not build on Tradovate market data. It is unconfirmed for Apex accounts and
appears to carry a substantial exchange licence fee. Use an independently licensed vendor through
the provider adapter interface. Tradovate remains a candidate only for read-only account data
(balances, positions, fills) in Phase 4, if official documentation confirms it is permitted;
manual entry and CSV import are the baseline.

## Choosing a real data provider

The adapter interface (`apps/api/app/adapters/marketdata/base.py`) needs two methods: fetch
closed 1-minute bars for a time range, and stream 1-minute bar updates. Any vendor licensed to
redistribute CME Globex data to you can be added without touching the rest of the system.

Databento was chosen for the first adapter because it publishes an HTTP API for CME Globex
(dataset `GLBX.MDP3`) with continuous front-month symbols. Forum users report a low monthly price
for non-professional live CME data, but that figure is user-reported; check Databento's pricing
and licence terms yourself.

To finish real data:

1. Get a Databento API key and set `APEX_MARKET_DATA_PROVIDER=databento`,
   `APEX_DATABENTO_API_KEY=...`.
2. Run the worker and confirm bars arrive and pass validation. The adapter is marked unverified
   in the UI until this has been done and the flag is changed in code.
3. For real-time bars, add a streaming adapter (Databento's live service uses a separate
   protocol). Until then everything from Databento is labelled HISTORICAL, not LIVE.

## Credentials

| Credential | Phase | Where it goes |
|---|---|---|
| `APEX_DATABENTO_API_KEY` (or another vendor's key) | 2 | API/worker environment |
| `APEX_ANTHROPIC_API_KEY` | 5 | API server environment |
| Tradovate API credentials (only if read-only access is confirmed as permitted) | 4 | API server environment, encrypted at rest |
| Economic calendar API key (provider TBD) | 5 | API server environment |
