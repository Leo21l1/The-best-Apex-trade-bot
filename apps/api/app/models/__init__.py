"""Import all models so Alembic autogenerate sees the full metadata."""

from app.models.audit import AuditLog
from app.models.user import Role, User

__all__ = ["AuditLog", "Role", "User"]
