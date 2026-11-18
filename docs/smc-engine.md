# Market Analyzer and Smart Money Concepts engine

PROJECT APEX has three functions: **Market Analyzer** (ES, NQ, MNQ), **News** (ForexFactory calendar,
re-evaluated every 60 s) and **Trading Journal**. It is analysis only: there is no broker connection and
no order can be submitted, modified or cancelled.

## Reference indicator and licence

The public *Smart Money Concepts [LuxAlgo]* Pine Script v5 indicator (© LuxAlgo, licensed
**CC BY-NC-SA 4.0**) was studied as a behavioural **comparison reference**. No Pine Script code was
copied or translated line by line. The engine (`apps/api/app/research/smc_structure.py`) is an
independent Python implementation of widely used, generic market-structure definitions. Attribution is
kept in the module docstring and here. If you redistribute this project, keep this attribution. Do not
copy the indicator's source into the project, because its NonCommercial and ShareAlike terms may not
be compatible with how you use or license PROJECT APEX.

## Definitions (as implemented)

| Concept | Rule | Known after |
|---|---|---|
| Pivot (size *k*) | Leg flips when bar *i−k*'s high exceeds the next *k* highs (pivot high), or its low is below the next *k* lows (pivot low). The initial leg is bearish, as in the reference. | close of bar *i* (= pivot + *k*) |
| Internal / swing layers | Internal *k* = 5, swing *k* = 50 (reference defaults). Each layer keeps its own trend. Internal breaks of a level that is also the current swing level are suppressed, so one move is not reported twice. | — |
| BOS / CHoCH | A close through the current pivot of a layer. With the layer's trend, or as the first break: **BOS**. Against it: **CHoCH**, and the trend flips. Each pivot can be broken once. | close of the break bar |
| HH / HL / LH / LL | A pivot compared with the previous pivot of the same kind and layer. | pivot confirmation |
| Strong / weak high/low | Highest high and lowest low since the last swing pivot. The high is *strong* in a bearish swing trend (otherwise weak); the low mirrors this. | each closed bar |
| Premium / discount | Price within that swing dealing range. Equilibrium band 47.5–52.5 %; premium above it, discount below it. | each closed bar |
| Order block | On each structure break: the candle with the lowest low (bullish) or highest high (bearish) between the broken pivot and the break. High-volatility candles (range ≥ 2 × ATR(200), or × cumulative mean range) have their high/low swapped, as the reference filter does. Status is **fresh**, **mitigated** (price traded back in, still valid) or **invalidated** (the wick or close, configurable, went through the far side). | break bar |
| Fair value gap | Three candles, confirmed at the close of the third. Bullish: low₃ > high₁ and close₂ > high₁. Size ≥ max(2 ticks, 0.15 × ATR(14)). Optional causal auto-threshold: the middle candle's body % must exceed 2 × its running mean. Status is **unmitigated**, **partially mitigated**, **fully mitigated** or **invalidated** (a close beyond the far side). Records retests and displacement. | third candle's close |
| Equal highs / lows | Consecutive size-3 pivots of the same kind within 0.1 × ATR(200). | second pivot's confirmation |
| Liquidity state | For a level, using bars after it was known: **approaching** (within 0.5 × ATR), **breached** (traded 1 tick beyond, no verdict yet), **swept and reclaimed** (a close back within 3 bars = failed breakout), **accepted beyond** (2 consecutive closes beyond). | verdict bar |
| Period levels | PDH/PDL, PWH/PWL and PMH/PML from completed trading days/weeks/months only. Overnight high/low and prior RTH high/low come from 1-minute bars. | start of the next period |

These are candle-geometry heuristics. Resting orders, stops and institutional activity are **not
observable** from OHLCV candles, and a detected zone is not a profitable strategy.

## Differences from the reference (documented, not claimed parity)

* **No look-ahead in FVGs.** The reference requests the FVG timeframe with
  `request.security(..., [close[1], open[1], time[1], high[0], low[0], time[0], high[2], low[2]], lookahead = barmerge.lookahead_on)`.
  On historical bars, `high[0]`/`low[0]` with `lookahead_on` and a higher timeframe are the values of
  the *completed* higher-timeframe candle. That candle had not finished at the time of the chart bar, so
  past gaps can appear earlier than they were knowable (repainting). APEX detects gaps only from CLOSED
  bars of each timeframe and timestamps them at the third candle's close.
* **MTF levels.** The reference uses `[high[1], low[1]]` with `lookahead_on`, the correct non-repainting
  idiom (the previous completed period). APEX does the same from completed daily bars.
* **Pivot labels.** The reference draws a pivot at its original candle as soon as the leg flips. APEX
  stores both the pivot's candle and its confirmation time, and never uses a pivot before confirmation.
* **Real-time bars.** The reference recalculates on the forming bar (`varip`, `barstate.isrealtime`).
  APEX analyzes **closed bars only**, so live results equal what a historical replay gives.
* **Equal highs/lows tolerance** has a half-tick floor, so exact equal prices on quiet bars still count.
* **Mitigation naming.** The reference deletes a block when the source crosses it. APEX keeps it as
  `invalidated`, and separates `mitigated` (touched, still valid).

## Look-ahead and repainting safeguards (tested)

`apps/api/tests/test_p27_smc_structure.py` and `test_p28_analyzer_news.py` test the following:
* Pivots are absent when the series ends one bar before their confirmation.
* Events, pivots and FVGs computed on truncated series equal the full-series results up to the cut.
* `analyzer.build` ignores bars after `now`, even when they are passed in. That test caught a real defect.
* Higher-timeframe bars are aggregated from 1-minute bars, and the forming bar is dropped.

## Multi-timeframe bias and confluence

* **Bias** comes from 1h and 15m swing structure. If the 1h swing layer is not yet confirmed, 1h internal
  structure is used and labelled as such. They agree, or one is neutral: that direction. They conflict:
  **NEUTRAL**. Missing or stale data: **WAIT / INSUFFICIENT DATA**. The 5m internal trend is reported
  separately as a *pullback* when it differs from the bias, which is not a contradiction.
* **Twelve factors** are grouped into families that vote once each, so BOS, CHoCH and trend state are not
  counted as independent proof:
  * *context*: 1h, 5m internal and 15m swing structure;
  * *location*: FVG, order block, premium/discount;
  * *trigger*: sweep-and-reclaim, strategy confirmation;
  * *conditions*: VWAP, volatility/volume, session;
  * *gates*: news, historical evidence, data.
* **Setup quality (0–100)** is a transparent checklist score: context 30, location 25, trigger 25,
  conditions up to 20. Any gate caps it at 25. It is **not a win probability** and was not tuned on
  historical results.
* **Strategies** (11, in 4 families) are shown as Confirmed, Developing, Conflicting, Invalidated or
  Not applicable, from the scanner's evaluations and recent signal outcomes.
* **MNQ and NQ** track the same Nasdaq-100 index, so their agreement is not independent confirmation. MNQ
  prices come only from MNQ bars. With none stored, MNQ shows DATA UNAVAILABLE and is never derived
  from NQ.

## News intelligence

* **Source sync:** the ForexFactory weekly export (`nfs.faireconomy.media`) is downloaded at most hourly.
  * The schedule and backoff live in PostgreSQL (`source_sync_state`), so restarts and several workers
    never download early.
  * A Redis lock serialises attempts.
  * Failures back off 5 → 10 → 20 → 40 min; HTTP 429 honours Retry-After.
  * Stored events are never deleted on failure.
  * Forecast and previous values are stored as published. **Actual** stays empty unless the source
    supplies it (the weekly export does not).
* **Every 60 s:** the worker and the News page re-evaluate the cached events against the clock.
  * Statuses: HIGH IMPACT UPCOMING (60 min), NEWS WINDOW ACTIVE (−15 … +5 min), POST-NEWS VOLATILITY
    (+5 … +15 min), NO MAJOR SCHEDULED EVENT NEARBY, or NEWS DATA STALE / UNAVAILABLE (last success
    older than 3 h). All windows are configurable.
  * The worker sends one alert per event and status.
* This is a **scheduled calendar, not breaking news**. Unscheduled headlines are not covered and are
  never scraped or fabricated. The direction of a reaction is never predicted.

## Journal

* **Manual entry:** single fills, or scaled entries with partial exits (quantity-weighted averages; entry
  and exit quantities must balance). Point values: ES $50, NQ $20, MNQ $2.
* **Fees** are stored once, separately from gross P&L.
* **R** = net P&L ÷ planned risk at the stop.
* **Stats:**
  * win rate, average win and loss, profit factor, expectancy;
  * daily, weekly and monthly results;
  * by strategy, instrument and hour (ET);
  * maximum drawdown and longest win/loss streaks.
* Records are labelled **MANUALLY ENTERED** unless imported from a broker CSV.

## Technical BUY/SELL setups (five-factor confluence)

Implemented in `apps/api/app/services/setups.py`; history and outcomes in `signal_tracker.py`.

**Five factors per direction**, each PASS / FAIL / UNKNOWN. UNKNOWN never counts as PASS.

| # | Factor | PASS when |
|---|---|---|
| 1 | Higher-timeframe bias | 1h/15m structure favours the direction and price is not in the adverse half of the 15m dealing range (premium for longs, discount for shorts). Daily context is shown. |
| 2 | Market structure | A confirmed 5m (or 1m) internal BOS/CHoCH in the direction within 60 min, and 5m internal structure in that direction. Anything else is FAIL ("developing") or, if the structure is opposite, FAIL. |
| 3 | Liquidity | Opposing liquidity was swept and reclaimed (a failed breakout), or price was accepted beyond liquidity in the direction, within 60 min. A rejection against the direction is FAIL. |
| 4 | FVG / order block | Price is retesting a still-valid zone of the direction on 5m/15m. A zone created by the structure-break candle is the same event, and is counted once (under factor 2). |
| 5 | Momentum / execution | Price on the right side of VWAP, the last 5m candle confirms or rejects, volatility/volume are not dead, and it is the New York morning or afternoon session. |

**Mandatory factors** per strategy must PASS whatever the score:

| Strategy | Mandatory factors |
|---|---|
| MTF / VWAP trend | 1 + 5 |
| Momentum pullback | 1 + 2 |
| ORB, volatility expansion | 5 |
| Breakout retest | 2 + 5 |
| Sweep reversal, mean reversion | 3 |
| Sweep + MSS | 2 + 3 |
| OB retest | 2 + 4 |
| FVG retest | 4 |

The strategy itself must also have triggered.

**TECHNICAL BUY/SELL SETUP CONFIRMED** requires all 12 of:
1. At least 4/5 confluences (`APEX_SIGNAL_MIN_CONFLUENCE`, 4 or 5).
2. Every mandatory factor PASS.
3. LIVE (or clearly labelled SIMULATED) fresh data.
4. Contract and tick known.
5. Calendar not stale.
6. No NEWS WINDOW ACTIVE or POST-NEWS VOLATILITY.
7. A valid entry zone that price has not already run away from.
8. A structural stop.
9. A realistic target.
10. Net R:R to TP1 of at least `APEX_MIN_NET_REWARD_RISK` (1.5).
11. No contradicting 5m structure.
12. A trigger younger than the expiry.

Otherwise the state is DEVELOPING BUY/SELL (3+ confluences or a partial strategy setup) or WAIT.

**Levels:**
* **Entry** is the strategy's entry and zone.
* **Stop** is the nearest structural invalidation beyond the zone (sweep extreme, the retested
  FVG/OB invalidation, a 5m pivot), at least max(4 ticks, 0.3 × ATR5m) away, minus a buffer of
  max(2 ticks, 0.1 × ATR). Only if there is none: the strategy stop.
* **TP1/TP2** are one tick in front of the nearest opposing liquidity (PDH/PDL, ONH/ONL, equal
  highs/lows, weekly/monthly), opposing zones and 15m pivots. They are never pushed further away to
  improve R:R. If the nearest one makes R:R too small, the setup does not confirm.
* Gross and net R:R are shown for both targets (commission round trip, entry slippage and stop
  slippage on the risk side).

**Evidence is separate from the technical state.** It is labelled VALIDATED, PROMISING BUT
UNVALIDATED, INSUFFICIENT EVIDENCE or NEGATIVE EVIDENCE, from the latest research study. Anything not
validated is shown as RESEARCH / UNVALIDATED. A technical confirmation never overrides it.

**Signal history (`setup_signals`):**
* Each candidate with ≥ 3/5 and valid levels is stored once:
  * qualified setups as CONFIRMED;
  * the rest as OBSERVED, which are research tiers and never shown as signals.
* `confirmed_at` is the close of the last 1-minute candle used, so the signal can never predate its
  information. The record stores the contract, strategy, engine version, all factors, levels, costs,
  news status and context.
* The original fields are never rewritten. Only the lifecycle columns change:
  * EXPIRED: not reached within 30 min;
  * MISSED: price left toward the target first;
  * INVALIDATED: an opposite 5m structure shift before entry;
  * hypothetical outcome after entry: TP1, stop or flat at 15:55 ET.
* Conservative fill rules: limit at the entry price; stop first when a bar touches both; TP1 needs a
  1-tick trade-through.

**Comparing 3/5, 4/5 and 5/5:**
* *Prospectively:* `GET /api/signals/stats` (also shown in Advanced analysis).
* *Historically on development data:*
  `python -m app.cli confluence-study --symbol ES --days 365 --holdout-sessions 50`. This excludes the
  previously inspected final holdout.
* The report shows counts, win rate with Wilson CI, average win/loss, net expectancy with bootstrap
  CI, profit factor, drawdown, longest loss streak, average R, and breakdowns by strategy, instrument,
  session, news and month.
* It only names a better threshold when each tier has at least 30 resolved outcomes and the 95%
  intervals do not overlap. Otherwise it says INCONCLUSIVE.
* The live threshold is never changed automatically.

## News calendar freshness (fix)

Preflight used to report the calendar's age from the events' `created_at`. Upserts never change that
column, so a calendar that synced every hour looked stale since the first insert. Freshness now comes
only from `source_sync_state.last_success_at`, the last successful download. A stale or missing calendar
is reported as STALE / UNAVAILABLE (FAIL) and blocks setup confirmation. Any sync failure, including a
database error, is recorded with exponential backoff, so a failure can never turn into a 60-second retry
loop.
