"""Import all models so Alembic autogenerate sees the full metadata."""

from app.models.account import AccountSnapshot, RuleRow
from app.models.audit import AuditLog
from app.models.calendar import EconomicEvent
from app.models.candle import CandleRow
from app.models.copilot import CopilotExchange
from app.models.research import BacktestRun, JournalTrade, PaperAccount
from app.models.signal import AlertRow, SignalRow
from app.models.user import Role, User

__all__ = [
    "AccountSnapshot",
    "AlertRow",
    "AuditLog",
    "BacktestRun",
    "CandleRow",
    "CopilotExchange",
    "EconomicEvent",
    "JournalTrade",
    "PaperAccount",
    "Role",
    "RuleRow",
    "SignalRow",
    "User",
]
