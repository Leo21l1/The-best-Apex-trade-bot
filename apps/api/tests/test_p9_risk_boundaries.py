"""Apex $50K EOD boundary scenarios: session rollover, DST, rising EOD threshold, exposure mixes.

Values follow the trader-supplied reference parameters; the rule profile itself stays UNVERIFIED.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from app.marketdata.calendar import trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.risk.engine import Position, RuleSet, Snapshot, account_state, contract_exposure, drawdown_threshold

RULES = RuleSet("apex", D(50000), D(3000), D(2000), D(1000), D(6))


def test_trading_day_rolls_at_1800_new_york_in_winter_and_summer() -> None:
    # Winter (EST, UTC-5): 17:59 ET = 22:59 UTC belongs to the same day; 18:00 ET starts the next.
    assert trading_day(datetime(2026, 11, 11, 22, 59, tzinfo=UTC)) == date(2026, 11, 11)
    assert trading_day(datetime(2026, 11, 11, 23, 0, tzinfo=UTC)) == date(2026, 11, 12)
    # Summer (EDT, UTC-4): the same wall-clock boundary is one UTC hour earlier.
    assert trading_day(datetime(2026, 7, 15, 21, 59, tzinfo=UTC)) == date(2026, 7, 15)
    assert trading_day(datetime(2026, 7, 15, 22, 0, tzinfo=UTC)) == date(2026, 7, 16)


def test_dst_transition_days_roll_at_local_1800() -> None:
    # 2026-03-08 (spring forward): Sunday evening 18:00 EDT = 22:00 UTC opens Monday's session.
    assert trading_day(datetime(2026, 3, 8, 21, 59, tzinfo=UTC)) == date(2026, 3, 8)
    assert trading_day(datetime(2026, 3, 8, 22, 0, tzinfo=UTC)) == date(2026, 3, 9)
    # 2026-11-01 (fall back): 18:00 EST = 23:00 UTC.
    assert trading_day(datetime(2026, 11, 1, 22, 59, tzinfo=UTC)) == date(2026, 11, 1)
    assert trading_day(datetime(2026, 11, 1, 23, 0, tzinfo=UTC)) == date(2026, 11, 2)


def test_rising_eod_balance_raises_threshold_and_is_enforced_intraday() -> None:
    eod = [(date(2026, 11, 9), D(50800)), (date(2026, 11, 10), D(51500))]
    assert drawdown_threshold(RULES, [b for _, b in eod]) == D(49500)
    now = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)  # Wed 11:00 ET
    # Down $900 intraday with an open position: within the daily limit, $1,100 above the threshold.
    st = account_state(RULES, Snapshot(now, D(51000), D(-400)), eod, now, INSTRUMENTS)
    assert st.equity == D(50600) and st.day_pnl == D(-900)
    assert st.daily_loss_remaining == D(100) and st.drawdown_room == D(1100)
    assert st.risk_budget == D(100) and not st.daily_loss_hit and not st.drawdown_breached
    # A further $150 loss hits the daily limit before the EOD threshold.
    st = account_state(RULES, Snapshot(now, D(51000), D(-550)), eod, now, INSTRUMENTS)
    assert st.daily_loss_hit and not st.drawdown_breached and st.risk_budget == D(0)


def test_drawdown_threshold_can_bind_before_daily_limit() -> None:
    eod = [(date(2026, 11, 9), D(50000))]
    now = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)
    # Previous days lost money: balance 48,600, threshold 48,000, so room (600) < daily allowance.
    st = account_state(RULES, Snapshot(now, D(48600), D(0)), [*eod, (date(2026, 11, 10), D(48600))], now, INSTRUMENTS)
    assert st.drawdown_threshold == D(48000)
    assert st.drawdown_room == D(600) and st.risk_budget == D(600)


def test_intraday_high_does_not_move_threshold_but_next_eod_does() -> None:
    now = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)
    st = account_state(RULES, Snapshot(now, D(52500), D(0)), [], now, INSTRUMENTS)
    assert st.drawdown_threshold == D(48000)  # intraday peak ignored
    later = now + timedelta(days=1)
    st = account_state(RULES, Snapshot(later, D(52500), D(0)), [(date(2026, 11, 11), D(52500))], later, INSTRUMENTS)
    assert st.drawdown_threshold == D(50500)


def test_exposure_limit_mixes_standard_and_micro() -> None:
    pos = lambda s, q: Position(s, q, D(5000))  # noqa: E731
    assert contract_exposure([pos("MES", 60)], INSTRUMENTS) == D(6)
    assert contract_exposure([pos("ES", 3), pos("MNQ", -30)], INSTRUMENTS) == D(6)
    now = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)
    st = account_state(
        RULES, Snapshot(now, D(50000), D(0), positions=(pos("ES", 5), pos("MES", 11))), [], now, INSTRUMENTS
    )
    assert st.contracts_in_use == D("6.1")
    assert any("exceeds the contract limit" in w for w in st.warnings)
