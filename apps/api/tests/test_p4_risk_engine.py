"""Risk Guardian arithmetic. Every expected value here is computed by hand."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.marketdata.instruments import INSTRUMENTS
from app.risk.engine import (
    AccountState,
    CostModel,
    Position,
    RuleSet,
    Snapshot,
    account_state,
    check_trade,
    contract_exposure,
    correlation_group,
    drawdown_threshold,
    loss_per_contract,
    max_contracts_for_risk,
    start_of_day_balance,
)

RULES = RuleSet("apex", D(50000), D(3000), D(2000), D(1000), D(6))
VERIFIED = RuleSet("apex", D(50000), D(3000), D(2000), D(1000), D(6), verified=True)
NOW = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)  # Wed 10:30 ET, trading day 2026-11-11
COSTS = CostModel(D("2.50"), D("1"))
ES, MES, NQ, CL = (INSTRUMENTS[s] for s in ("ES", "MES", "NQ", "CL"))


def snap(balance: str, upl: str = "0", age_min: int = 1, positions: tuple[Position, ...] = ()) -> Snapshot:
    return Snapshot(NOW - timedelta(minutes=age_min), D(balance), D(upl), "manual", positions)


def state(
    balance: str = "50000",
    upl: str = "0",
    eod: list[tuple[date, D]] | None = None,
    rules: RuleSet = RULES,
    **kw: object,
) -> AccountState:
    return account_state(rules, snap(balance, upl, **kw), eod or [], NOW, INSTRUMENTS)  # type: ignore[arg-type]


# --- EOD drawdown threshold -----------------------------------------------------------------


def test_initial_threshold_is_start_minus_drawdown() -> None:
    assert drawdown_threshold(RULES, []) == D(48000)


def test_threshold_trails_only_end_of_day_highs() -> None:
    assert drawdown_threshold(RULES, [D(50500), D(51200), D(50800)]) == D(49200)
    # Losing days never lower it.
    assert drawdown_threshold(RULES, [D(51200), D(49000)]) == D(49200)
    # A balance below the start never lowers it below the initial threshold.
    assert drawdown_threshold(RULES, [D(49500)]) == D(48000)


def test_intraday_peak_does_not_move_eod_threshold() -> None:
    """An intraday run-up to $52,000 that closes the day flat must not raise the threshold.

    (Under an intraday trailing drawdown it would be $50,000; under EOD it stays $48,000.)
    """
    peak_today = state("50000", "2000")  # equity touched 52,000 intraday...
    assert peak_today.equity == D(52000)
    assert peak_today.drawdown_threshold == D(48000)  # ...threshold unchanged
    assert peak_today.drawdown_room == D(4000)


def test_optional_stop_level_caps_the_threshold() -> None:
    capped = RuleSet("apex", D(50000), D(3000), D(2000), D(1000), D(6), drawdown_stop_level=D(50100))
    assert drawdown_threshold(capped, [D(53500)]) == D(50100)
    assert drawdown_threshold(capped, [D(51000)]) == D(49000)


# --- daily loss and budget -------------------------------------------------------------------


def test_start_of_day_balance_uses_previous_eod_only() -> None:
    eod = [(date(2026, 11, 9), D(50400)), (date(2026, 11, 10), D(50900)), (date(2026, 11, 11), D(1))]
    assert start_of_day_balance(RULES, eod, date(2026, 11, 11)) == D(50900)
    assert start_of_day_balance(RULES, [], date(2026, 11, 11)) == D(50000)


def test_account_state_figures() -> None:
    eod = [(date(2026, 11, 10), D(51000))]
    s = state("50700", "-150", eod=eod)
    assert s.known and not s.stale
    assert s.equity == D(50550)
    assert s.drawdown_threshold == D(49000)
    assert s.drawdown_room == D(1550)
    assert s.start_of_day_balance == D(51000)
    assert s.day_pnl == D(-450)
    assert s.daily_loss_remaining == D(550)
    assert s.risk_budget == D(550)  # min(1550, 550)
    assert s.profit_to_target == D(2300)
    assert not s.drawdown_breached and not s.daily_loss_hit and not s.target_reached


def test_budget_limited_by_drawdown_when_close_to_threshold() -> None:
    s = state("48300")
    assert s.drawdown_room == D(300)
    assert s.daily_loss_remaining == D(0)  # down 1,700 today from 50,000
    assert s.daily_loss_hit and s.risk_budget == D(0)


def test_profitable_day_does_not_increase_daily_allowance() -> None:
    s = state("50600")
    assert s.daily_loss_remaining == D(1000)


def test_breach_and_daily_limit_flags() -> None:
    breached = state("48000")
    assert breached.drawdown_breached and breached.risk_budget == D(0)
    assert any("drawdown threshold" in w for w in breached.warnings)
    hit = state("49000")
    assert hit.daily_loss_hit and not hit.drawdown_breached


def test_target_reached_uses_closed_balance() -> None:
    assert state("53000").target_reached
    assert not state("52999", "500").target_reached  # open profit does not count


def test_unknown_account_is_not_treated_as_available() -> None:
    s = account_state(RULES, None, [], NOW, INSTRUMENTS)
    assert s.known is False and s.risk_budget is None and s.equity is None
    assert any("UNKNOWN" in w for w in s.warnings)


def test_staleness_rules() -> None:
    assert state(age_min=31).stale is True
    assert state(age_min=29).stale is False
    saturday = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
    friday_close = Snapshot(datetime(2026, 11, 13, 21, 59, tzinfo=UTC), D(50000))
    # Market closed: a reading taken 1 minute before Friday's close is still current...
    assert account_state(RULES, friday_close, [], saturday, INSTRUMENTS).stale is False
    # ...but Sunday evening (new trading day open) it is.
    sunday_open = datetime(2026, 11, 15, 23, 45, tzinfo=UTC)
    assert account_state(RULES, friday_close, [], sunday_open, INSTRUMENTS).stale is True
    # A Thursday reading viewed on Saturday is stale (Friday's session ran since).
    thursday = Snapshot(datetime(2026, 11, 12, 21, 0, tzinfo=UTC), D(50000))
    assert account_state(RULES, thursday, [], saturday, INSTRUMENTS).stale is True
    # During the daily 17:00-18:00 ET break, age is measured from the 17:00 close.
    brk = datetime(2026, 11, 11, 22, 45, tzinfo=UTC)
    at_close = Snapshot(datetime(2026, 11, 11, 21, 50, tzinfo=UTC), D(50000))
    assert account_state(RULES, at_close, [], brk, INSTRUMENTS).stale is False


def test_unverified_rules_are_warned() -> None:
    s = state(rules=RuleSet("apex", D(50000), D(3000), D(2000), D(1000), D(6), unverified_rules=("Daily loss limit",)))
    assert any("UNVERIFIED" in w and "Daily loss limit" in w for w in s.warnings)
    assert not any("UNVERIFIED" in w for w in state(rules=VERIFIED).warnings)


# --- contracts, costs, sizing ----------------------------------------------------------------


def test_exposure_counts_micros_as_tenths() -> None:
    pos = [Position("ES", 2, D(5000)), Position("MES", -5, D(5000)), Position("MNQ", 3, D(18000))]
    assert contract_exposure(pos, INSTRUMENTS) == D("2.8")


def test_cost_and_loss_per_contract() -> None:
    assert COSTS.round_trip(ES) == D("30.00")  # 2 x (2.50 + 12.50)
    assert loss_per_contract(ES, D("5000"), D("4996"), COSTS) == (D("200.00"), D("230.00"))
    micro = CostModel(D("0.60"), D("1"))
    assert loss_per_contract(MES, D("5000"), D("4996"), micro) == (D("20.00"), D("23.70"))
    assert loss_per_contract(CL, D("70.00"), D("69.80"), COSTS) == (D("200.00"), D("225.00"))


def test_max_contracts_floors() -> None:
    assert max_contracts_for_risk(D(230), D(1000)) == 4
    assert max_contracts_for_risk(D(250), D(1000)) == 4
    assert max_contracts_for_risk(D(251), D(1000)) == 3
    assert max_contracts_for_risk(D(230), D(0)) == 0
    assert max_contracts_for_risk(D(0), D(1000)) == 0


def test_correlation_groups() -> None:
    assert correlation_group("MNQ") == correlation_group("ES") == "US equity index"
    assert correlation_group("MCL") == "Crude oil" and correlation_group("GC") == "Gold"


# --- trade checks ----------------------------------------------------------------------------


def chk(
    st: AccountState,
    inst=ES,
    direction="long",
    entry="5000",
    stop="4996",
    target="5008",
    qty=1,
    rules: RuleSet = RULES,
):  # type: ignore[no-untyped-def]
    return check_trade(
        rules, st, inst, direction, D(entry), D(stop), D(target) if target else None, qty, COSTS, INSTRUMENTS
    )


def test_six_outputs_for_a_clean_trade() -> None:
    c = chk(state(), qty=2)
    assert c.stop_ticks == 16
    assert c.dollar_risk == D("400.00")  # 1. 16 ticks x $12.50 x 2
    assert c.max_size == 4  # 2. budget 1000 / 230 = 4 (contract limit allows 6)
    assert c.max_size_by_risk == 4 and c.max_size_by_contract_limit == 6
    assert c.reward_risk == D("2.00")  # 3. 8 points / 4 points
    assert c.loss_after_costs == D("460.00")  # 4. (200 + 30) x 2
    assert c.budget_used_pct == D("46.0")  # 5. 460 / 1000
    assert c.violations == ()  # 6.
    assert c.verdict == "no_violation_found"
    assert c.may_be_called_compliant is False  # rule profile unverified
    assert "UNVERIFIED" in c.summary
    assert any("risk budget" in w for w in c.warnings)  # above the 25% advisory cap


def test_compliance_label_requires_verified_rules_and_fresh_data() -> None:
    assert chk(state(rules=VERIFIED), rules=VERIFIED).may_be_called_compliant is True
    stale = chk(state(rules=VERIFIED, age_min=90), rules=VERIFIED)
    assert stale.verdict == "cannot_confirm" and stale.may_be_called_compliant is False
    unknown = account_state(VERIFIED, None, [], NOW, INSTRUMENTS)
    c = chk(unknown, rules=VERIFIED)
    assert c.verdict == "cannot_confirm" and c.max_size is None and c.budget_used_pct is None


def test_daily_loss_violation() -> None:
    c = chk(state("49400"), qty=2)  # 400 remaining today; trade loses 460
    assert c.verdict == "violation"
    assert any("daily loss allowance" in v for v in c.violations)


def test_drawdown_violation_when_room_is_smaller_than_the_loss() -> None:
    eod = [(date(2026, 11, 10), D(51800))]  # threshold 49,800
    c = chk(state("50000", eod=eod), qty=1)  # room 200, loss 230
    assert any("drawdown threshold" in v for v in c.violations)
    assert c.max_size == 0


def test_contract_limit_counts_existing_and_micro_positions() -> None:
    st = state(positions=(Position("ES", 5, D(5000), D(4990)), Position("MES", 5, D(5000), D(4990))))
    assert st.contracts_in_use == D("5.5")
    assert chk(st, inst=MES, qty=5).violations == ()  # 5.5 + 0.5 = 6.0
    over = chk(st, inst=MES, qty=6)  # 5.5 + 0.6 = 6.1
    assert any("Contract limit" in v for v in over.violations)
    assert chk(st, inst=MES).max_size_by_contract_limit == 5
    assert chk(st).max_size_by_contract_limit == 0  # no room for a standard contract


def test_correlated_exposure_warning() -> None:
    st = state(positions=(Position("NQ", 1, D(18000), D(17950)),))
    c = chk(st)
    assert any("correlated US equity index" in w and "NQ +1" in w for w in c.warnings)
    assert not any("correlated" in w for w in chk(st, direction="short", stop="5004", target="4992").warnings)


def test_already_breached_account_blocks_everything() -> None:
    c = chk(state("47990"))
    assert c.verdict == "violation" and c.max_size == 0


def test_short_trade_arithmetic() -> None:
    c = chk(state(), inst=NQ, direction="short", entry="18000", stop="18010", target="17970", qty=3)
    assert c.stop_ticks == 40 and c.dollar_risk == D("600.00")  # 40 x $5 x 3
    assert c.cost == D("45.00") and c.loss_after_costs == D("645.00")  # (2.50 + 5.00) x 2 x 3
    assert c.reward_risk == D("3.00")


@pytest.mark.parametrize(
    ("direction", "entry", "stop", "target", "message"),
    [
        ("long", "5000", "5001", None, "stop must be below entry"),
        ("short", "5000", "4999", None, "stop must be below entry"),
        ("long", "5000", "4996", "4999", "target must be beyond entry"),
        ("long", "5000.10", "4996", None, "multiple of the tick size"),
        ("sideways", "5000", "4996", None, "direction"),
    ],
)
def test_invalid_trade_inputs(direction: str, entry: str, stop: str, target: str | None, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        chk(state(), direction=direction, entry=entry, stop=stop, target=target)
    with pytest.raises(ValueError, match="quantity"):
        chk(state(), qty=0)
