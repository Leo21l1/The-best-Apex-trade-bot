"""Research validation standards.

A strategy may produce RANKED opportunities only after its backtest evidence meets these
thresholds. Until the backtesting laboratory (Phase 6) records results, every strategy is
unvalidated and the ranked leaderboard is empty by design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidationStandards:
    min_trades: int = 100
    min_out_of_sample_trades: int = 30
    min_profit_factor: float = 1.20
    min_expectancy_r: float = 0.05
    max_drawdown_r: float = 15.0
    require_walk_forward: bool = True


STANDARDS = ValidationStandards()


@dataclass(frozen=True, slots=True)
class ValidationStatus:
    validated: bool
    reason: str
    sample_size: int | None = None
    expectancy_r: float | None = None
    profit_factor: float | None = None


def strategy_validation(strategy_id: str) -> ValidationStatus:
    """Validation status for a strategy. No backtest evidence exists yet."""
    return ValidationStatus(False, "No backtest evidence recorded (backtesting arrives in Phase 6).")
