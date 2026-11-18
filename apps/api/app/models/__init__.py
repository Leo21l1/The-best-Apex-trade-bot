"""Import all models so Alembic autogenerate sees the full metadata."""

from app.models.account import AccountSnapshot, RuleRow
from app.models.audit import AuditLog
from app.models.calendar import EconomicEvent, NewsItem, SourceSyncState
from app.models.candle import CandleRow
from app.models.copilot import CopilotExchange
from app.models.research import (
    BacktestRun,
    JournalAttachment,
    JournalEdit,
    JournalNote,
    JournalTrade,
    PaperAccount,
    ResearchStudy,
    TradePlan,
)
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
    "JournalAttachment",
    "JournalEdit",
    "JournalNote",
    "JournalTrade",
    "NewsItem",
    "PaperAccount",
    "ResearchStudy",
    "Role",
    "RuleRow",
    "SignalRow",
    "SourceSyncState",
    "TradePlan",
    "User",
]
