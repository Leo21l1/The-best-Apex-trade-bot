# Research methodology: rollover, walk-forward validation, execution and evidence

Implementation: `apps/api/app/research/` (`rollover.py`, `backtest.py`, `stats.py`, `apex_replay.py`,
`study.py`), stored by `apps/api/app/services/research_study.py`, shown on **Strategy Research**
(`/research`). Run with `python -m app.cli research-study` (see "Reproduce").

Nothing here places orders. Live Apex/Tradovate execution stays disabled; the Risk Guardian is unchanged.

## 1. Data and futures rollover

* Source: stored 1-minute `ohlcv-1m` bars from Databento GLBX.MDP3, continuous front month `ROOT.v.0`
  (roll by previous-day volume). Prices are **not back-adjusted**: each roll is a price jump.
* Usable session: >= 300 of the 390 regular-hours minutes (scaled on early-close days, CME calendar)
  and >= 200 bars overall - the same rule as the backfill and `data-audit`.
* Roll calendar (ES, NQ, micros): quarterly Mar/Jun/Sep/Dec; conventional roll date = Thursday 8 days
  before the third-Friday expiry (e.g. 2026-09-10, 2026-12-10).
* **Policy:** trade dates from one session before to two sessions after each roll date are excluded
  (the volume-based switch may happen a day either side). The remaining history is split into
  **per-contract segments**; strategies run on each segment separately, so indicators, previous-day
  levels and 15m/1h bars never mix two contracts, and no trade can span a roll (positions are also
  flat by 15:55 ET). Fills and P&L use the real, unadjusted prices of the traded contract.
* The contract label (e.g. `ESZ6`) is **inferred** from the roll calendar; stored bars do not carry
  the contract id. `detect_discontinuities` checks the data independently: a session-open gap larger
  than 8x the median outside a roll window is reported as an unexplained discontinuity.
* Daylight saving and holidays come from the exchange calendar (`pandas_market_calendars`); all
  session arithmetic uses UTC instants (tested across the 2026-11-01 change).

## 2. Look-ahead protection

* Each evaluation sees only 1-minute bars that opened before the 5-minute close, and only 15m/1h
  bars fully closed by then; indicators are computed on that prefix.
* Entry is the open of the next 1-minute bar (plus slippage). Relative volume uses prior sessions only.
* Regression test `test_no_look_ahead_trades_do_not_depend_on_later_bars`: trades closed before a
  cut-off are identical with or without the later bars.
* Strategy rules and the five configurations are fixed in advance: nothing is fitted to the data.

## 3. Execution model (1 contract)

| Item | Rule |
|---|---|
| Entry | market at the next 1-minute open after the signal bar closes, 1 tick adverse slippage |
| Stop | when a bar trades at/through it: fill at the stop, or at the open if it gapped through; 1 tick slippage |
| Target | limit: only when price trades one tick THROUGH it; filled at the target, no slippage |
| Same bar touches stop and target | counted as the stop (intrabar order unknown: conservative) |
| Time | flat at 15:55 ET at the bar open (1 tick slippage); signals only 09:35-15:30 ET |
| Overlap | one position at a time; signals while in a position are skipped and counted |
| Commission | $2.50 per side per standard contract (configurable) |
| Audit fields | gross P&L at ideal fills, commission, slippage $, net P&L, initial $ risk, R, MFE/MAE (1-minute highs/lows from the entry bar to the exit bar, inclusive: conservative) |

Stress scenarios (re-execute the same signals): 2 ticks slippage; commission x1.5 + 2 ticks; +1 tick on
stop fills; entry one bar late. No bid/ask spread or order-book depth is modelled (not in the data).

## 4. Periods (chronological, pre-registered)

Usable sessions after roll exclusion, in time order:

* **Research**: first 100 sessions (in-sample; reported, never used for selection)
* **Walk-forward**: consecutive blocks of 25 sessions (out-of-sample; rankings use these)
* **Holdout**: last 50 sessions (confirmation only; never used to choose)

With fewer than 250 sessions the same proportions are scaled down and the study is labelled
**PRELIMINARY**. Forward paper trading is a separate, later stage.

## 5. Statistics

* Win = net P&L > 0 after all costs; loss < 0; breakeven = 0. **Win rate = wins / all trades.**
* Win-rate interval: Wilson 95%. The win-rate ranking sorts by the **lower bound**, so 9/10
  (lower bound 59.6%) does not outrank 400/600 (62.8%). Rows with < 30 trades are flagged.
* Expectancy = average net $ per trade; interval = bootstrap (seed 7); the classification uses the
  Bonferroni-adjusted level for `strategies x configurations` comparisons.
* A high win rate is not profitability: 85% winners that each make $50 and 15% losers that each lose
  $300 lose $2.50 per trade. Rankings A (win rate) and B (risk-adjusted expectancy) are separate.

## 6. Evidence classification (per strategy x configuration x instrument)

* **Insufficient evidence**: simulated data, or < 30 walk-forward trades.
* **Negative evidence**: walk-forward expectancy <= 0.
* **Promising but unvalidated**: positive, but at least one requirement below is unmet (listed).
* **Validated for paper-trading consideration** - all of: >= 100 trades in total; walk-forward
  expectancy interval above 0 at the adjusted level; profit factor >= 1.20; average R >= 0.05;
  >= 60% of walk-forward blocks positive; still positive with 2 ticks slippage; holdout >= 10 trades
  and positive; data verdict SUFFICIENT; full 250-session plan (not preliminary).

Live signals: with `APEX_REQUIRE_VALIDATED_STRATEGY=true` (default) a setup can only be LONG/SHORT
READY when the latest study classifies its strategy as validated **for the configuration the live
gate uses** (`t1_net15` at the default 1.5 minimum). Otherwise the confirmation says WAIT and lists
the missing evidence. The production strategy is never switched automatically.

## 7. Apex evaluation replay

Out-of-sample trades (walk-forward + holdout), 1 contract, through the rule profile in Apex Account
Health: end-of-day trailing drawdown (Risk Guardian's `drawdown_threshold`), checked intraday with
each trade's MAE; profit target; daily loss limit as a day stop. Consecutive, non-overlapping
attempts; pass rate with a Wilson interval. **The rule values are labelled UNVERIFIED until each rule
is verified in the app against an official apextraderfunding.com page** (this environment could not
reach that site). A configured daily loss limit is not assumed to be a provider rule.

## 8. Reproduce

```powershell
docker compose exec api python -m app.cli data-audit --symbols ES,NQ --days 400
docker compose exec api python -m app.cli strategy-inventory --out /tmp/strategy-inventory.md
docker compose exec api python -m app.cli research-study --symbols ES,NQ --days 400 --workers 2 --out-dir /tmp/research_out
docker compose cp api:/tmp/research_out ./research_out
```

Outputs per instrument: `study_<SYM>.json` (all statistics), `leaderboard_<SYM>.csv`,
`trades_<SYM>.csv` (every simulated trade with audit fields), `report_<SYM>.md`. The study is also
stored in the database and shown on `/research`. Same bars + same settings = same results (no
randomness except the seeded bootstrap).
