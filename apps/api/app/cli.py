"""Operational CLI.

python -m app.cli create-admin --email you@example.com      (prompts for password)
python -m app.cli reset-db --yes                              (non-production only)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from pydantic import ValidationError
from sqlalchemy import select, text

from app.core.config import Environment, get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker
from app.core.security import hash_password
from app.models.user import Role, User
from app.schemas.auth import BootstrapRequest
from app.services.audit import record_audit


async def create_admin(email: str, password: str) -> int:
    try:
        BootstrapRequest(email=email, password=password)
    except ValidationError as exc:
        print(f"Invalid input: {exc.errors()[0]['msg']}", file=sys.stderr)
        return 2
    async with get_sessionmaker()() as s:
        if await s.scalar(select(User).where(User.email == email.lower())):
            print("User already exists", file=sys.stderr)
            return 1
        user = User(email=email.lower(), password_hash=hash_password(password), role=Role.ADMIN.value)
        s.add(user)
        await s.flush()
        await record_audit(s, action="cli.create_admin", outcome="success", actor_id=user.id)
        await s.commit()
    print(f"Created admin {email.lower()}")
    return 0


async def reset_db() -> int:
    if get_settings().environment == Environment.PRODUCTION:
        print("Refusing to reset a production database", file=sys.stderr)
        return 1
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE backtest_runs, paper_accounts, journal_trades, trade_plans, copilot_exchanges, "
                "economic_events, "
                "account_snapshots, rules, alerts, signals, "
                "candles, audit_logs, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    print("Database tables truncated")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apex")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from stdin instead of prompting",
    )
    rd = sub.add_parser("reset-db")
    rd.add_argument("--yes", action="store_true", required=True)
    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.cmd == "create-admin":
                pw = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass()
                return await create_admin(args.email, pw)
            return await reset_db()
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
