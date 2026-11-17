"""Drives paper accounts from stored 1-minute bars. No broker or network access of any kind."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.marketdata.calendar import floor_minute, trading_day
from app.models.research import JournalTrade, PaperAccount
from app.paper.engine import PaperBook, PaperConfig, new_state
from app.research.scanner import load_bars
from app.risk.profile import load_rules, to_ruleset


async def config_from_rules(session: AsyncSession, settings: Settings) -> PaperConfig:
    rules = to_ruleset(await load_rules(session))
    return PaperConfig(
        starting_balance=float(rules.starting_balance),
        max_drawdown=float(rules.max_drawdown),
        daily_loss_limit=float(rules.daily_loss_limit),
        max_contracts=float(rules.max_contracts),
        commission_standard=settings.cost_commission_per_side_standard,
        commission_micro=settings.cost_commission_per_side_micro,
        slippage_ticks=settings.cost_slippage_ticks_per_side,
    )


async def get_account(session: AsyncSession, settings: Settings) -> PaperAccount:
    """The single paper account for the configured data provider (created on first use)."""
    acct = await session.scalar(
        select(PaperAccount)
        .where(PaperAccount.provider == settings.market_data_provider)
        .order_by(PaperAccount.id)
        .limit(1)
    )
    if acct is None:
        cfg = await config_from_rules(session, settings)
        acct = PaperAccount(
            name="Paper account", provider=settings.market_data_provider, state=new_state(cfg), cursor={}
        )
        session.add(acct)
        await session.flush()
    return acct


async def process(session: AsyncSession, acct: PaperAccount, now: datetime) -> int:
    """Feed new closed bars for every symbol with working orders or positions. Returns bars processed."""
    book = PaperBook(copy.deepcopy(acct.state))
    symbols = {o["symbol"] for o in book.s["orders"] if o["status"] in ("working", "partially_filled")}
    symbols |= set(book.s["positions"])
    processed = 0
    cursor = dict(acct.cursor or {})
    until = floor_minute(now)  # bars opening before this minute are closed
    for sym in sorted(symbols):
        since_iso = cursor.get(sym)
        earliest = min(
            (
                datetime.fromisoformat(o["created_at"])
                for o in book.s["orders"]
                if o["symbol"] == sym and o["status"] in ("working", "partially_filled")
            ),
            default=until - timedelta(minutes=5),
        )
        since = datetime.fromisoformat(since_iso) + timedelta(minutes=1) if since_iso else floor_minute(earliest)
        for bar in await load_bars(session, acct.provider, sym, since, until):
            book.on_bar(sym, bar)
            cursor[sym] = bar.ts.isoformat()
            processed += 1
    # Market orders fill at the open of a bar as soon as it starts (see PaperBook.on_open).
    try:
        import json

        from app.core.redis import get_redis
        from app.marketdata.feed import LATEST_KEY

        for sym in sorted(symbols):
            raw = await get_redis().get(f"{LATEST_KEY}{sym}")
            if not raw:
                continue
            latest = json.loads(raw)
            ts = datetime.fromtimestamp(latest["t"], tz=UTC)
            if latest.get("provider") != acct.provider or ts > now:
                continue
            before = len(book.s["fills"])
            book.on_open(sym, ts, float(latest["o"]))
            book.s["last_prices"][sym] = float(latest["c"])  # live mark for unrealised P&L
            if len(book.s["fills"]) != before or book.s["positions"]:
                processed += 1
    except (OSError, ValueError, KeyError, RedisError):
        pass  # Redis unavailable: the closed-bar path still fills orders
    if processed:
        acct.state = book.s
        acct.cursor = cursor
        await journal_closed(session, acct)
    return processed


async def journal_closed(session: AsyncSession, acct: PaperAccount) -> None:
    """Copy closed paper round trips into the journal (idempotent by source key)."""
    for c in acct.state["closed"]:
        exit_t = datetime.fromisoformat(c["exit_time"])
        stmt = (
            insert(JournalTrade)
            .values(
                source_key=f"paper:{acct.id}:{c['id']}",
                source="paper",
                trading_date=trading_day(exit_t),
                symbol=c["symbol"],
                contract=c["symbol"],
                direction=c["direction"],
                quantity=c["qty"],
                entry_time=datetime.fromisoformat(c["entry_time"]),
                exit_time=exit_t,
                entry_price=c["entry_price"],
                exit_price=c["exit_price"],
                gross_pnl=c["gross_pnl"],
                fees=c["fees"],
                net_pnl=round(c["gross_pnl"] - c["fees"], 2),
                mistakes=[],
                slippage=c.get("slippage", 0.0),
                plan_id=c.get("plan_id"),
            )
            .on_conflict_do_nothing(index_elements=["source_key"])
        )
        await session.execute(stmt)


def now_utc() -> datetime:
    return datetime.now(UTC)
