"""Research validation standards.

A strategy may produce RANKED opportunities only after its latest backtest on real historical data
(never simulated data) meets these thresholds, including out-of-sample and walk-forward checks.
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


NO_EVIDENCE = ValidationStatus(False, "No completed backtest on real historical data yet.")


def strategy_validation(strategy_id: str, known: dict[str, ValidationStatus] | None = None) -> ValidationStatus:
    """Validation status from the latest qualifying experiment (see ``services.research.load_validations``)."""
    return (known or {}).get(strategy_id, NO_EVIDENCE)
