# Phase 5 report — AI copilot, economic calendar, briefings

Date: 2026-11-17

## 1. Completed features

- **AI copilot** (`app/ai/`): Claude Opus 5.5 through the Anthropic Python SDK (Messages API), with
  a self-hosted tool loop because every tool reads the local database.
  - 12 read-only tools: system status, market snapshot, recent bars, radar, opportunities,
    strategy specs, account risk, trade risk check, rule profile, economic calendar, alerts,
    session activity. Each result is labelled `verified_app_data`, `statistical_estimate` or
    `unavailable`. No tool can place, modify or cancel orders, change settings or read credentials.
  - Risk numbers come only from the deterministic Risk Guardian tools; the system prompt forbids the
    model from computing them.
  - Answers are structured as Verified data / Estimates / Interpretation / Missing information, and
    the UI shows every tool call with its full result.
  - Settings: effort `medium` (explicit), prompt caching on the stable system prompt and tool list
    (volatile time stamp in the user turn), server-side refusal fallback (`fallbacks: "default"`)
    enabled, refusal / pause / max-tokens handling, capped tool rounds, per-user rate limit,
    timeout, typed provider-error mapping, and an audit record of every question, answer and tool call.
- **Economic calendar**: import of official `.ics` files (line folding, time zones, all-day events,
  escapes, duplicate-safe by UID), optional periodic fetch of configured official ICS URLs, manual
  entry, deterministic classification (CPI, FOMC, NFP, PCE, GDP, PPI, retail sales, claims, JOLTS,
  ISM, ECI, EIA inventories, earnings) with affected markets, configurable pre/post windows.
- **News integration**: event-approaching alerts (once per event), news exposure on opportunity
  candidates and on Risk Guardian checks; "unknown" when no calendar is imported.
- **Session briefing** (no AI): phase (premarket / live / post-market / closed), checklist (data
  class, rule verification, account freshness, high-importance events), prior-day and overnight
  levels per instrument, today's events, account budget, candidate count.
- **Dashboard**: AI Copilot page (suggested questions, tool trace, history, data-disclosure
  notice), Economic Calendar page (by day in ET, importance filter, in-window highlight, import and
  manual entry), briefing on Overview, "ask the copilot" links from opportunity details.
- **Fixed during testing**: a race that dropped the first copilot question while history loaded;
  a mobile horizontal overflow from the briefing table (grid items now shrink); `get_recent_bars`
  now validates input before checking data availability; a lazy-load error after rollback in the
  copilot endpoint.

## 3–4. Tests executed and results

| Suite | Result |
|---|---|
| API lint and format | pass |
| API tests | **224 passed** |
| Web lint, typecheck, build | pass |
| Web unit tests | **35 passed** |
| End-to-end | **18 passed** (run twice) |

Copilot tests run against a mocked Anthropic transport (unit/API) and a scripted fake Anthropic
server (end-to-end) that the real SDK calls through `ANTHROPIC_BASE_URL`. They check the request
shape (model, effort, caching, fallbacks, no forced tool choice), tool results returned together
with matching ids, deterministic numbers in tool output, refusal / pause / max-tokens handling,
the round cap, unknown-tool and bad-input errors, history sanitising, rate limiting, error mapping
and auditing, every tool on an empty system, and that no tool exposes credentials.

## 5. Known limitations

- **The copilot has never called the real Anthropic API.** No API key was available. All copilot
  behaviour was tested against mocks and a scripted fake, so answer quality is untested.
- When you ask the copilot something, the question and the tool results the model requests (which
  can include entered balances) are sent to Anthropic. Credentials and session tokens are not.
- Official calendar sites are blocked from the build environment; ICS parsing was tested on
  hand-written files in the published format, not on a downloaded BLS or Fed file.
- Event importance is a fixed classification, not a forecast. Earnings need manual entry.
- All-day ICS entries are anchored at 08:30 ET and labelled as assumed.
- Executed-trade analysis ("why did this strategy fail", "summarize my session" with real trades)
  needs trade import from Phase 6.
- Conversation history is sent back as text only; long conversations are capped at 20 turns.

## 6. Required credentials

`APEX_ANTHROPIC_API_KEY` for the copilot. Optional `APEX_CALENDAR_ICS_URLS` (no key).

## 8. Next phase (6 — backtesting, paper trading, journal)
