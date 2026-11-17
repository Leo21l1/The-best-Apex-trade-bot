"""Apex Risk Guardian: deterministic account-risk arithmetic.

Pure functions over Decimals. Nothing here calls an LLM, a broker or the network.

END-OF-DAY drawdown (this module) is NOT an intraday trailing drawdown:

* the threshold is derived only from END-OF-DAY balances:
  ``threshold = max(starting_balance, highest EOD balance) - max_drawdown``
  (optionally capped at a configured stop level);
* intraday equity highs never move the threshold;
* the threshold is treated as enforced in real time against current equity (balance plus
  unrealised P&L). That is the conservative reading while the official methodology is unverified.

All outputs are advisory. They cannot guarantee protection against account failure, and they are
only as good as the manually entered account data they are computed from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from app.marketdata.calendar import is_market_open, trading_day
from app.marketdata.instruments import Instrument

ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class RuleSet:
    profile: str
    starting_balance: Decimal
    profit_target: Decimal
    max_drawdown: Decimal
    daily_loss_limit: Decimal
    #: Maximum position in standard contracts; one micro counts as 0.1.
    max_contracts: Decimal
    #: Optional level above which the threshold stops trailing (None = not configured).
    drawdown_stop_level: Decimal | None = None
    #: True only when every required rule has a verification record.
    verified: bool = False
    unverified_rules: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    #: Signed quantity: positive long, negative short.
    quantity: int
    avg_price: Decimal
    stop: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    as_of: datetime
    balance: Decimal
    unrealized_pnl: Decimal = ZERO
    source: str = "manual"
    positions: tuple[Position, ...] = ()


def drawdown_threshold(rules: RuleSet, eod_balances: Sequence[Decimal]) -> Decimal:
    """Liquidation threshold from end-of-day balances only."""
    high = max([rules.starting_balance, *eod_balances])
    threshold = high - rules.max_drawdown
    if rules.drawdown_stop_level is not None:
        threshold = min(threshold, rules.drawdown_stop_level)
    return threshold


def start_of_day_balance(rules: RuleSet, eod: Sequence[tuple[date, Decimal]], today: date) -> Decimal:
    """Balance the current trading day started from: the latest EOD balance before ``today``."""
    prior = [(d, b) for d, b in eod if d < today]
    return max(prior)[1] if prior else rules.starting_balance


@dataclass(frozen=True, slots=True)
class AccountState:
    as_of: datetime
    profile: str
    rules_verified: bool
    #: False when no snapshot exists; every monetary field is then None.
    known: bool
    source: str | None = None
    snapshot_at: datetime | None = None
    snapshot_age_seconds: float | None = None
    stale: bool = True
    trading_date: date | None = None
    balance: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    equity: Decimal | None = None
    highest_eod_balance: Decimal | None = None
    drawdown_threshold: Decimal | None = None
    drawdown_room: Decimal | None = None
    start_of_day_balance: Decimal | None = None
    day_pnl: Decimal | None = None
    daily_loss_remaining: Decimal | None = None
    #: min(drawdown room, daily loss remaining), never negative.
    risk_budget: Decimal | None = None
    profit_to_target: Decimal | None = None
    target_reached: bool = False
    drawdown_breached: bool = False
    daily_loss_hit: bool = False
    contracts_in_use: Decimal = ZERO
    positions: tuple[Position, ...] = ()
    warnings: tuple[str, ...] = ()


def last_open_instant(now: datetime, max_lookback: timedelta = timedelta(days=4)) -> datetime | None:
    """``now`` if the market is open, else the end of the most recent open minute (holidays not modelled)."""
    if is_market_open(now):
        return now
    minute = now.replace(second=0, microsecond=0)
    limit = now - max_lookback
    while minute > limit:
        minute -= timedelta(minutes=1)
        if is_market_open(minute):
            return minute + timedelta(minutes=1)
    return None


def contract_exposure(positions: Sequence[Position], instruments: dict[str, Instrument]) -> Decimal:
    """Open exposure in standard-contract equivalents (a micro counts as 0.1)."""
    total = ZERO
    for p in positions:
        inst = instruments.get(p.symbol)
        total += abs(p.quantity) * (inst.standard_equivalent if inst else Decimal("1"))
    return total


def account_state(
    rules: RuleSet,
    snapshot: Snapshot | None,
    eod: Sequence[tuple[date, Decimal]],
    now: datetime,
    instruments: dict[str, Instrument],
    stale_after: timedelta = timedelta(minutes=30),
) -> AccountState:
    warnings: list[str] = []
    if not rules.verified:
        warnings.append(
            "Rule profile is UNVERIFIED (" + ", ".join(rules.unverified_rules) + "). "
            "Figures use provisional values and no trade can be called compliant."
        )
    if snapshot is None:
        warnings.append("No account snapshot. Account risk is UNKNOWN, not available.")
        return AccountState(now, rules.profile, rules.verified, False, warnings=tuple(warnings))

    today = trading_day(now)
    age = max(0.0, (now - snapshot.as_of).total_seconds())
    # Age is measured against the last moment the market was open: a reading taken just before
    # Friday's close is still current on Saturday, but goes stale once the next session runs.
    reference = last_open_instant(now)
    stale = reference is None or (reference - snapshot.as_of) > stale_after
    if stale:
        warnings.append(
            f"Account snapshot is stale ({int(age // 60)} min old). Actual balance and positions may "
            "differ; treat remaining risk as unknown."
        )
    warnings.append("Account data was entered manually or imported; broker-side values are not confirmed.")

    balances = [b for _, b in eod]
    threshold = drawdown_threshold(rules, balances)
    equity = snapshot.balance + snapshot.unrealized_pnl
    sod = start_of_day_balance(rules, eod, today)
    day_pnl = equity - sod
    room = equity - threshold
    daily_remaining = rules.daily_loss_limit + min(day_pnl, ZERO)
    budget = max(min(room, daily_remaining), ZERO)
    breached = room <= ZERO
    daily_hit = daily_remaining <= ZERO
    if breached:
        warnings.append("Equity is at or below the drawdown threshold on the entered figures.")
    if daily_hit:
        warnings.append("Daily loss limit reached on the entered figures.")
    in_use = contract_exposure(snapshot.positions, instruments)
    if in_use > rules.max_contracts:
        warnings.append(f"Open exposure {in_use} exceeds the contract limit {rules.max_contracts}.")
    if snapshot.positions and any(p.stop is None for p in snapshot.positions):
        warnings.append("Some open positions have no stop recorded; loss at stop is unknown.")

    return AccountState(
        as_of=now,
        profile=rules.profile,
        rules_verified=rules.verified,
        known=True,
        source=snapshot.source,
        snapshot_at=snapshot.as_of,
        snapshot_age_seconds=age,
        stale=stale,
        trading_date=today,
        balance=snapshot.balance,
        unrealized_pnl=snapshot.unrealized_pnl,
        equity=equity,
        highest_eod_balance=max([rules.starting_balance, *balances]),
        drawdown_threshold=threshold,
        drawdown_room=room,
        start_of_day_balance=sod,
        day_pnl=day_pnl,
        daily_loss_remaining=max(daily_remaining, ZERO),
        risk_budget=budget,
        profit_to_target=rules.starting_balance + rules.profit_target - snapshot.balance,
        target_reached=snapshot.balance - rules.starting_balance >= rules.profit_target,
        drawdown_breached=breached,
        daily_loss_hit=daily_hit,
        contracts_in_use=in_use,
        positions=snapshot.positions,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_per_side: Decimal
    slippage_ticks_per_side: Decimal

    def round_trip(self, inst: Instrument) -> Decimal:
        """Assumed cost to open and close ONE contract."""
        return (2 * (self.commission_per_side + self.slippage_ticks_per_side * inst.tick_value)).quantize(CENT)


def loss_per_contract(inst: Instrument, entry: Decimal, stop: Decimal, costs: CostModel) -> tuple[Decimal, Decimal]:
    """(price risk in dollars, loss after assumed costs) for one contract stopped out."""
    price_risk = inst.dollars_between(entry, stop).quantize(CENT)
    return price_risk, price_risk + costs.round_trip(inst)


def max_contracts_for_risk(per_contract_loss: Decimal, budget: Decimal) -> int:
    """Largest whole number of contracts whose loss at the stop (after costs) fits the budget."""
    if per_contract_loss <= ZERO or budget <= ZERO:
        return 0
    return int((budget / per_contract_loss).to_integral_value(rounding=ROUND_FLOOR))


#: Instruments whose prices tend to move together; same-direction positions add up.
CORRELATION_GROUPS: dict[str, frozenset[str]] = {
    "US equity index": frozenset({"ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K"}),
    "Crude oil": frozenset({"CL", "MCL"}),
    "Gold": frozenset({"GC", "MGC"}),
}


def correlation_group(symbol: str) -> str | None:
    return next((name for name, members in CORRELATION_GROUPS.items() if symbol in members), None)


@dataclass(frozen=True, slots=True)
class TradeCheck:
    symbol: str
    direction: str
    quantity: int
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    stop_ticks: Decimal
    #: 1. Estimated dollar risk (price move to the stop, all contracts).
    dollar_risk: Decimal
    cost: Decimal
    #: 4. Estimated loss after costs.
    loss_after_costs: Decimal
    #: 3. Reward-to-risk on price (None without a target).
    reward_risk: Decimal | None
    #: 2. Maximum size allowed under the configured constraints (None if account unknown).
    max_size: int | None
    max_size_by_risk: int | None
    max_size_by_contract_limit: int
    #: 5. Share of the remaining risk budget this trade would consume (None if unknown).
    budget_used_pct: Decimal | None
    #: 6. Rule violations on the configured values.
    violations: tuple[str, ...]
    warnings: tuple[str, ...]
    #: "violation" | "cannot_confirm" | "no_violation_found"
    verdict: str
    #: Never True while the rule profile is unverified or account data is stale/unknown.
    may_be_called_compliant: bool
    summary: str = field(default="")


def check_trade(
    rules: RuleSet,
    state: AccountState,
    inst: Instrument,
    direction: str,
    entry: Decimal,
    stop: Decimal,
    target: Decimal | None,
    quantity: int,
    costs: CostModel,
    instruments: dict[str, Instrument],
    max_trade_fraction: Decimal = Decimal("0.25"),
) -> TradeCheck:
    """Evaluate a proposed manual trade against the configured rule values."""
    if direction not in ("long", "short"):
        raise ValueError("direction must be 'long' or 'short'")
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    s = 1 if direction == "long" else -1
    for name, px in (("entry", entry), ("stop", stop), ("target", target)):
        if px is not None and (px <= 0 or px % inst.tick_size != 0):
            raise ValueError(f"{name} must be a positive multiple of the tick size {inst.tick_size}")
    if (entry - stop) * s <= 0:
        raise ValueError("stop must be below entry for a long and above entry for a short")
    if target is not None and (target - entry) * s <= 0:
        raise ValueError("target must be beyond entry in the trade direction")

    price_risk, per_contract = loss_per_contract(inst, entry, stop, costs)
    dollar_risk = price_risk * quantity
    cost = costs.round_trip(inst) * quantity
    loss = per_contract * quantity
    rr = None if target is None else (abs(target - entry) / abs(entry - stop)).quantize(CENT)

    violations: list[str] = []
    warnings: list[str] = []

    proposed = quantity * inst.standard_equivalent
    free = rules.max_contracts - state.contracts_in_use
    by_limit = max(int((free / inst.standard_equivalent).to_integral_value(rounding=ROUND_FLOOR)), 0)
    if proposed > free:
        violations.append(
            f"Contract limit: {state.contracts_in_use} open + {proposed} proposed exceeds "
            f"{rules.max_contracts} standard-contract equivalents."
        )

    by_risk: int | None = None
    used: Decimal | None = None
    if state.known and state.risk_budget is not None:
        by_risk = max_contracts_for_risk(per_contract, state.risk_budget)
        if state.drawdown_breached:
            violations.append("Equity is already at or below the drawdown threshold.")
        if state.daily_loss_hit:
            violations.append("Daily loss limit already reached for this trading day.")
        if state.target_reached:
            warnings.append("Profit target already reached on the entered balance; new risk is unnecessary.")
        if state.drawdown_room is not None and loss >= state.drawdown_room > ZERO:
            violations.append(f"Loss at stop ${loss} would reach the drawdown threshold (room ${state.drawdown_room}).")
        if state.daily_loss_remaining is not None and loss > state.daily_loss_remaining > ZERO:
            violations.append(
                f"Loss at stop ${loss} exceeds the remaining daily loss allowance ${state.daily_loss_remaining}."
            )
        if state.risk_budget > ZERO:
            used = (loss / state.risk_budget * 100).quantize(Decimal("0.1"))
            if loss > state.risk_budget * max_trade_fraction and not violations:
                warnings.append(
                    f"Trade uses {used}% of the remaining risk budget (advisory cap {max_trade_fraction * 100:.0f}%)."
                )
    else:
        warnings.append("Account state unknown: size limits by risk cannot be computed.")

    group = correlation_group(inst.symbol)
    if group:
        same = [p for p in state.positions if correlation_group(p.symbol) == group and p.quantity * s > 0]
        if same:
            warnings.append(
                f"Adds to existing {direction} exposure in correlated {group} positions: "
                + ", ".join(f"{p.symbol} {p.quantity:+d}" for p in same)
                + "."
            )
        opposite = [p for p in state.positions if p.symbol == inst.symbol and p.quantity * s < 0]
        if opposite:
            warnings.append(f"Opposes an open {inst.symbol} position; net exposure differs from this check.")

    confirmable = state.known and not state.stale
    if violations:
        verdict = "violation"
        summary = "Violates configured rule values."
    elif not confirmable:
        verdict = "cannot_confirm"
        summary = "Cannot confirm: account data is missing or stale."
    else:
        verdict = "no_violation_found"
        summary = (
            "No violation of the configured values found."
            if rules.verified
            else "No violation of the configured values found, but the rule profile is UNVERIFIED."
        )
    max_size = None if by_risk is None else min(by_risk, by_limit)
    return TradeCheck(
        symbol=inst.symbol,
        direction=direction,
        quantity=quantity,
        entry=entry,
        stop=stop,
        target=target,
        stop_ticks=inst.ticks_between(entry, stop),
        dollar_risk=dollar_risk,
        cost=cost,
        loss_after_costs=loss,
        reward_risk=rr,
        max_size=max_size,
        max_size_by_risk=by_risk,
        max_size_by_contract_limit=by_limit,
        budget_used_pct=used,
        violations=tuple(violations),
        warnings=tuple(warnings),
        verdict=verdict,
        may_be_called_compliant=verdict == "no_violation_found" and rules.verified and confirmable,
        summary=summary,
    )
