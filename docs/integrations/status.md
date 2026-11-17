# Integration status

| Integration | Status | Notes |
|---|---|---|
| Market data provider | Not connected | Phase 2 builds a provider-independent adapter interface first. A provider is chosen only once its licence covers this use (real-time CME data for non-professional display). |
| Tradovate read-only | Not researched yet | Phase 2 will review Tradovate's official API documentation for read-only account and market-data access, and whether an Apex-provisioned account may use it. Nothing is assumed until verified. |
| Economic calendar | Not connected | Phase 5. Requires an authorised source. |
| Claude API (copilot) | Not configured | Phase 5. `APEX_ANTHROPIC_API_KEY` is read only by the API server and is never sent to the browser. |

## Credentials that will be needed

| Credential | Phase | Where it goes |
|---|---|---|
| `APEX_ANTHROPIC_API_KEY` | 5 | API server environment |
| Market data provider key (provider TBD) | 2 | API server environment |
| Tradovate API credentials (only if read-only access is verified as permitted) | 2/4 | API server environment, encrypted at rest |
| Economic calendar API key (provider TBD) | 5 | API server environment |
