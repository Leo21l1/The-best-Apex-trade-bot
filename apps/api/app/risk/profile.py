"""Apex rule profile: provisional seed values, persistence and verification records.

Seed values are PROVISIONAL REFERENCE VALUES from the trader's configuration. Nothing is marked
verified by this code: a rule becomes verified only when an administrator records the official
source URL they checked it against. Editing a rule's value clears its verification.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import RuleRow
from app.risk.engine import RuleSet

PROFILE = "apex-50k-eod-evaluation"
PROFILE_LABEL = "Apex Trader Funding · $50K · End-of-Day (EOD) Evaluation"
_EOD_EVAL = "https://apextraderfunding.com/help-center/eod-accounts/eod-evaluations/"
_SIZING = "https://apextraderfunding.com/help-center/additional-helpful-items/position-sizing-evaluation/"
_EOD_DD = "https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-drawdown-explained/"
_HELP = "https://apextraderfunding.com/help-center/"

# (key, label, kind, provisional value, required, suggested official page)
SEED: tuple[tuple[str, str, str, str, bool, str], ...] = (
    ("starting_balance", "Starting balance", "money", "50000", True, _EOD_EVAL),
    ("profit_target", "Evaluation profit target", "money", "3000", True, _EOD_EVAL),
    ("max_drawdown", "Maximum EOD drawdown", "money", "2000", True, _EOD_EVAL),
    ("daily_loss_limit", "Daily loss limit", "money", "1000", True, _EOD_EVAL),
    ("max_contracts", "Maximum standard contracts (1 micro = 0.1)", "contracts", "6", True, _SIZING),
    (
        "drawdown_stop_level",
        "Balance level at which the threshold stops trailing (blank = none)",
        "money",
        "",
        False,
        _EOD_DD,
    ),
    (
        "drawdown_method",
        "EOD drawdown methodology",
        "text",
        "Threshold = highest end-of-day balance - maximum drawdown; recalculated once per day at the "
        "close; treated here as enforced in real time against equity.",
        True,
        _EOD_DD,
    ),
    (
        "daily_loss_method",
        "Daily loss methodology",
        "text",
        "Measured from the balance at the start of the trading day, including unrealised P&L; "
        "reaching it pauses trading for the session.",
        True,
        _EOD_EVAL,
    ),
    (
        "session_times",
        "Daily reset and settlement times",
        "text",
        "Trading day ends 17:00 ET (positions flat before the close); new day from 18:00 ET.",
        True,
        _EOD_EVAL,
    ),
    (
        "unrealized_pnl",
        "Unrealised P&L treatment",
        "text",
        "Counted toward drawdown and daily loss (conservative assumption).",
        True,
        _EOD_DD,
    ),
    (
        "micro_equivalence",
        "Contract limits and micro equivalents",
        "text",
        "10 micro contracts count as 1 standard contract (6 standard or 60 micro, or combinations).",
        True,
        _SIZING,
    ),
    ("news_trading", "News trading restrictions", "text", "Unknown. Check the official rules.", True, _HELP),
    (
        "position_close",
        "Position close requirements",
        "text",
        "Unknown. Check the official rules.",
        True,
        _HELP,
    ),
    (
        "passing_criteria",
        "Evaluation passing criteria",
        "text",
        "Reach the profit target without breaching the drawdown. Time limit and minimum days: "
        "check the official rules.",
        True,
        _EOD_EVAL,
    ),
    (
        "prohibited_methods",
        "Prohibited trading methods",
        "text",
        "Unknown. Check the official rules.",
        True,
        _HELP,
    ),
    (
        "automation_policy",
        "Automation and third-party software policy",
        "text",
        "Unknown. PROJECT APEX places no orders regardless.",
        True,
        _HELP,
    ),
)
NUMERIC_KEYS = ("starting_balance", "profit_target", "max_drawdown", "daily_loss_limit", "max_contracts")


async def ensure_profile(session: AsyncSession) -> None:
    """Insert any missing seed rules (unverified). Existing rows are never overwritten."""
    existing = set(await session.scalars(select(RuleRow.key).where(RuleRow.profile == PROFILE)))
    for key, label, kind, value, required, url in SEED:
        if key not in existing:
            session.add(
                RuleRow(
                    profile=PROFILE,
                    key=key,
                    label=label,
                    kind=kind,
                    value=value,
                    required=required,
                    verified=False,
                    suggested_source_url=url,
                )
            )
    await session.flush()


async def load_rules(session: AsyncSession) -> list[RuleRow]:
    await ensure_profile(session)
    rows = (await session.scalars(select(RuleRow).where(RuleRow.profile == PROFILE))).all()
    order = {k[0]: i for i, k in enumerate(SEED)}
    return sorted(rows, key=lambda r: order.get(r.key, 999))


def parse_value(kind: str, raw: str, *, allow_blank: bool = False) -> str:
    """Validate and normalise a rule value. Raises ValueError with a user-facing message."""
    raw = raw.strip()
    if kind == "text":
        if not raw:
            raise ValueError("Value must not be empty")
        return raw[:1000]
    if not raw:
        if allow_blank:
            return ""
        raise ValueError("Value must not be empty")
    try:
        number = Decimal(raw.replace(",", "").replace("$", ""))
    except InvalidOperation as exc:
        raise ValueError("Value must be a number") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("Value must be a positive number")
    if kind == "contracts" and number != number.to_integral_value():
        raise ValueError("Contracts must be a whole number")
    return format(number.normalize(), "f")


def to_ruleset(rows: list[RuleRow]) -> RuleSet:
    by = {r.key: r for r in rows}
    unverified = tuple(r.label for r in rows if r.required and not r.verified)
    stop = by.get("drawdown_stop_level")
    return RuleSet(
        profile=PROFILE,
        **{k: Decimal(by[k].value) for k in NUMERIC_KEYS},
        drawdown_stop_level=Decimal(stop.value) if stop and stop.value else None,
        verified=not unverified,
        unverified_rules=unverified,
    )
