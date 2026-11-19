# Strategy playbook

Generated from the code (`python -m app.cli strategy-playbook`). Live rules are unchanged; H1-H4 are experimental research hypotheses evaluated only by `confluence-study`. H1: location optional for continuation/breakout. H2: major-level targets for continuation/breakout. H3: 15m internal-structure fallback for the bias. H4: the strategy's own stop with its second designed target as the exit.

Every strategy shares: closed candles only; strategy triggers 09:35-15:30 ET; confirmation needs the New York session, no NEWS WINDOW ACTIVE / POST-NEWS VOLATILITY, a fresh calendar, 4/5 confluences, its mandatory factors, a valid structural plan and net R:R >= 1.5 to TP1; setups expire 30 minutes after confirmation and are invalidated by an opposite 5m structure shift before entry.

**Levels:** Entry: the strategy's entry and zone. Stop: nearest structural invalidation beyond the zone (sweep extreme, the retested FVG/OB invalidation, 5m/15m pivot) at least max(4 ticks, 0.3 x ATR5m) away, minus a buffer max(2 ticks, 0.1 x ATR); otherwise the strategy stop. TP1/TP2: one tick before the nearest opposing liquidity, opposing FVG/OB or 15m pivot (H2: major levels only for continuation/breakout). Net R:R to TP1 >= 1.5.

**Historical evidence:** none of the eleven strategies is validated. The latest audited confluence studies (Dec 2025 - Sep 2026 development data) show negative or statistically inconclusive results after deduplication, one-position-at-a-time and the $1,000 risk cap. Earlier hypotheses (ES MTF Trend T1, NQ Breakout Retest T2) remain unvalidated hypotheses.

## Trend continuation

Trades with an established trend after a pullback. Premium/discount location works against it by construction (longs in an uptrend usually sit in the premium half): see hypothesis H1. Liquidity sweeps and zone retests are optional context, not its trigger.

### VWAP Trend Continuation (`vwap_trend`)

- **Regime:** Trend: EMA20 beyond EMA50, close beyond VWAP, VWAP rising/falling over 6 bars.
- **Entry (trigger):** Bar tests VWAP or EMA20 (wick within 0.25 ATR) and closes back beyond EMA20 in the trend direction with a with-trend body, 09:45-15:30 ET.
- **Mandatory confluence:** Higher-timeframe bias, Momentum / execution (plus the trigger)
- **Optional confluence:** Market structure, Liquidity, FVG / order block
- **Invalidation:** A 5m close on the wrong side of VWAP.
- **Strategy stop / targets (research backtests):** Extreme of the last 3 bars minus/plus 0.25 ATR. / 1.5R and 2.5R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H1 (location optional), H2 (major-level targets), H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Momentum Pullback (`momentum_pullback`)

- **Regime:** Momentum: EMA20 beyond EMA50 and RSI(14) reached >= 60 (long) / <= 40 (short) within the last 12 bars.
- **Entry (trigger):** At least 2 of the previous 3 bars closed against the trend, and the current bar tags EMA20 and closes beyond it with a with-trend body.
- **Mandatory confluence:** Higher-timeframe bias, Market structure (plus the trigger)
- **Optional confluence:** Liquidity, FVG / order block, Momentum / execution
- **Invalidation:** A 5m close beyond EMA50 against the trend.
- **Strategy stop / targets (research backtests):** Extreme of the last 5 bars minus/plus 0.1 ATR. / 1.5R and 2.5R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H1 (location optional), H2 (major-level targets), H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Multi-Timeframe Trend Alignment (`mtf_trend`)

- **Regime:** EMA20 beyond EMA50 in the same direction on 5-minute, 15-minute and 1-hour bars.
- **Entry (trigger):** 5m close beyond EMA20 and beyond the highest high / lowest low of the previous 3 bars.
- **Mandatory confluence:** Higher-timeframe bias, Momentum / execution (plus the trigger)
- **Optional confluence:** Market structure, Liquidity, FVG / order block
- **Invalidation:** Loss of alignment on the 15-minute timeframe.
- **Strategy stop / targets (research backtests):** Extreme of the last 5 bars minus/plus 0.25 ATR. / 1.5R and 3R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H1 (location optional), H2 (major-level targets), H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

## Breakout

Trades acceptance beyond a range or level. Its natural liquidity evidence is 'accepted beyond', not a sweep-and-reclaim; an FVG retest is optional. Nearby minor opposing zones often cap TP1: see H2.

### Opening Range Breakout (`orb`)

- **Regime:** Opening-range width between 0.5 and 4.0 ATR (not a dead or exhausted open).
- **Entry (trigger):** First 5m close beyond the 09:30-09:45 ET range (previous close inside it) with bar volume >= 1.2 x SMA20(volume), between 09:45 and 11:30 ET.
- **Mandatory confluence:** Momentum / execution (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Market structure, Liquidity, FVG / order block
- **Invalidation:** A 5m close back inside the opening range before entry.
- **Strategy stop / targets (research backtests):** Opening-range midpoint. / 1R and 2R from entry.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H1 (location optional), H2 (major-level targets), H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Breakout and Retest (`breakout_retest`)

- **Regime:** A consolidation level: the highest high / lowest low of bars t-45..t-6.
- **Entry (trigger):** A close beyond the level occurred in bars t-5..t-1; the current bar retests the level (wick within 0.25 ATR of it) and closes beyond it with a with-trend body.
- **Mandatory confluence:** Market structure, Momentum / execution (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Liquidity, FVG / order block
- **Invalidation:** A 5m close back through the level by more than 0.25 ATR.
- **Strategy stop / targets (research backtests):** Level minus/plus 0.75 ATR. / 1.5R and 3R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H1 (location optional), H2 (major-level targets), H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Volatility Expansion (`vol_expansion`)

- **Regime:** Squeeze: Bollinger(20, 2) width in bars t-6..t-1 touched the lowest 20% of its last 100 values.
- **Entry (trigger):** Current bar range >= 1.5 ATR and close outside the Bollinger band; trade in the direction of the close.
- **Mandatory confluence:** Momentum / execution (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Market structure, Liquidity, FVG / order block
- **Invalidation:** A 5m close back inside the band beyond the middle band.
- **Strategy stop / targets (research backtests):** Bollinger middle band (SMA20). / 1.5R and 2.5R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H1 (location optional), H2 (major-level targets), H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

## Reversal

Trades a failed move back into range. A sweep-and-reclaim and favourable location are central to the idea, so premium/discount stays required.

### Liquidity Sweep Reversal (`sweep_reversal`)

- **Regime:** Any; requires a defined reference level (prior-day high/low or overnight high/low).
- **Entry (trigger):** Bar trades at least 1 tick through a reference level and closes back inside it, with the rejection wick >= 50% of the bar range. Trade against the sweep.
- **Mandatory confluence:** Liquidity (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Market structure, FVG / order block, Momentum / execution
- **Invalidation:** Any trade beyond the sweep extreme.
- **Strategy stop / targets (research backtests):** Sweep bar extreme plus/minus 0.1 ATR. / 1R and 2R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Mean Reversion (`mean_reversion`)

- **Regime:** Range: |EMA20 - EMA50| <= 0.5 ATR (no established trend).
- **Entry (trigger):** Bar extreme beyond VWAP -/+ 2 sigma, RSI(14) <= 30 (long) / >= 70 (short), and the bar closes back toward VWAP (reversal body). Reward to VWAP must be >= 1R.
- **Mandatory confluence:** Liquidity (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Market structure, FVG / order block, Momentum / execution
- **Invalidation:** EMA20/EMA50 separation exceeds 1 ATR (trend emerging).
- **Strategy stop / targets (research backtests):** Signal bar extreme minus/plus 0.5 ATR. / Target 1: VWAP. Target 2: VWAP +/- 1 sigma on the far side.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

## Smart Money Concepts

Trades structure shifts and returns to FVGs / order blocks after liquidity is taken. Location and zone validity are central.

### Liquidity Sweep + Structure Shift (SMC) (`smc_sweep_mss`)

- **Regime:** Any; needs a confirmed sweep rejection followed by a change of character.
- **Entry (trigger):** Within the last 6 bars: a sell-side (buy-side) sweep with a CONFIRMED REJECTION, then a bullish (bearish) CHoCH/MSS that closes after the sweep. Enter at the close.
- **Mandatory confluence:** Market structure, Liquidity (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, FVG / order block, Momentum / execution
- **Invalidation:** A close beyond the sweep extreme.
- **Strategy stop / targets (research backtests):** Sweep extreme plus/minus 1 tick. / Nearest unswept opposing liquidity level if at least 1R away, then 2R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Order Block Retest with Trend (SMC) (`smc_ob_retest`)

- **Regime:** 1-hour structure trend in the trade direction.
- **Entry (trigger):** Latest 5-minute bar trades into an unmitigated-or-once-retested order block formed by a structure break in the trade direction and closes back outside it on the trend side.
- **Mandatory confluence:** Market structure, FVG / order block (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Liquidity, Momentum / execution
- **Invalidation:** A close through the far side of the order block.
- **Strategy stop / targets (research backtests):** Far side of the order block plus/minus 0.1 ATR. / Nearest opposing liquidity level if at least 1R away, else 1.5R and 2.5R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

### Fair Value Gap Retest with Displacement (SMC) (`smc_fvg_retest`)

- **Regime:** 5-minute structure trend in the trade direction; gap created by a displacement leg.
- **Entry (trigger):** Latest bar trades into a partially filled (< 100%) gap aligned with the structure trend, which was confirmed within the last 24 bars, and closes on the gap's trend side.
- **Mandatory confluence:** FVG / order block (plus the trigger)
- **Optional confluence:** Higher-timeframe bias, Market structure, Liquidity, Momentum / execution
- **Invalidation:** A close through the far side of the gap.
- **Strategy stop / targets (research backtests):** Far side of the gap plus/minus 0.1 ATR. / 1.5R and 2.5R.
- **Setup-engine stop / targets (live):** structural, see Levels above.
- **Time exit:** Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal.
- **Hypotheses that apply:** H3 (15m internal fallback), H4 (native stop + T2)
- **Evidence:** RESEARCH / UNVALIDATED.

