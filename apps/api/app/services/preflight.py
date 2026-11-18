"""Morning preflight: one read-only report of everything the 9:00 ET workflow depends on.

Every check reads the running stack (database, Redis, worker heartbeat, feed status, stored bars,
calendar, account snapshot, rule profile, research studies). Nothing is downloaded, written or
changed. Secrets are never printed. Results: PASS, WARN (works, needs attention) or FAIL (blocks
actionable recommendations).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.marketdata.calendar import ET
from app.marketdata.status import read_feed_status
from app.models.calendar import EconomicEvent
from app.services.health import check_redis, check_worker
from app.services.session_intel import market_status

LIVE_MAX_AGE_S = 180


def _line(name: str, result: str, detail: str) -> dict[str, str]:
    return {"check": name, "result": result, "detail": detail}


async def run_preflight(session: AsyncSession, symbols: list[str], now: datetime | None = None) -> dict[str, Any]:
    from app.risk.profile import load_rules
    from app.services.account import current_state
    from app.services.research_study import latest

    s = get_settings()
    now = now or datetime.now(UTC)
    out: list[dict[str, str]] = []
    # Database and migrations
    try:
        ver = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        bars = (await session.execute(text("SELECT count(*) FROM candles"))).scalar()
        out.append(_line("PostgreSQL", "PASS", f"connected; migration {ver}; {bars:,} stored bars"))
    except Exception as exc:  # report, never crash the preflight
        out.append(_line("PostgreSQL", "FAIL", f"{type(exc).__name__}: cannot query the database"))
        return {"checks": out, "ok": False, "at": now.isoformat()}
    r = await check_redis()
    out.append(_line("Redis", "PASS" if r.state.value == "ok" else "FAIL", r.detail or r.state.value))
    w = await check_worker()
    out.append(_line("Worker", "PASS" if w.state.value == "ok" else "FAIL", w.detail or w.state.value))
    # Feed
    feed = await read_feed_status() or {}
    prov = s.market_data_provider
    if prov != "databento":
        out.append(
            _line(
                "Market-data provider",
                "FAIL" if prov == "none" else "WARN",
                f"provider is '{prov}' (live trading analysis needs 'databento')",
            )
        )
    state, quality = feed.get("state", "not reporting"), (feed.get("quality") or "unknown").upper()
    out.append(
        _line(
            "Live feed",
            "PASS" if state == "streaming" and quality == "LIVE" else "FAIL",
            f"state {state}, data class {quality}"
            + (f"; last provider error: {feed.get('provider_last_error')}" if feed.get("provider_last_error") else "")
            + (f"; last error: {feed.get('last_error')}" if feed.get("last_error") else ""),
        )
    )
    for sym in symbols:
        row = (
            await session.execute(
                text(
                    "SELECT ts, close, quality FROM candles WHERE provider = :p AND symbol = :s "
                    "ORDER BY ts DESC LIMIT 1"
                ),
                {"p": prov, "s": sym},
            )
        ).first()
        mk = market_status(sym, now)
        contract = ((feed.get("contracts") or {}).get(sym) or {}).get("contract")
        if row is None:
            out.append(_line(f"{sym} data", "FAIL", "no stored bars"))
            continue
        age = (now - (row[0] + timedelta(minutes=1))).total_seconds()
        when = row[0].astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
        desc = f"last {row[1]} at {when} ({int(age)} s old, {str(row[2]).upper()})" + (
            f", contract {contract}" if contract else ""
        )
        if not mk["open"]:
            out.append(_line(f"{sym} data", "WARN", f"market closed ({mk['reason']}); {desc}"))
        elif age <= LIVE_MAX_AGE_S and str(row[2]).lower() == "live":
            out.append(_line(f"{sym} data", "PASS", desc))
        else:
            out.append(_line(f"{sym} data", "FAIL", f"STALE or not live while the market is open: {desc}"))
    # News calendar
    ev = (
        await session.execute(
            select(func.count())
            .select_from(EconomicEvent)
            .where(EconomicEvent.ts >= now - timedelta(hours=1), EconomicEvent.ts <= now + timedelta(days=1))
        )
    ).scalar() or 0
    ff = (
        await session.execute(select(func.max(EconomicEvent.created_at)).where(EconomicEvent.source == "forexfactory"))
    ).scalar()
    total = (await session.execute(select(func.count()).select_from(EconomicEvent))).scalar() or 0
    if not total:
        out.append(
            _line(
                "News calendar",
                "FAIL",
                "no economic calendar stored: news status is UNKNOWN, READY is blocked "
                "(Calendar page > ForexFactory > Sync now)",
            )
        )
    else:
        out.append(
            _line(
                "News calendar",
                "PASS" if ff and now - ff < timedelta(days=2) else "WARN",
                f"{ev} events in the next 24 h; last ForexFactory sync "
                + (ff.astimezone(ET).strftime("%Y-%m-%d %H:%M ET") if ff else "never"),
            )
        )
    # Account and rules
    rules, st = await current_state(session, now)
    if not st.known:
        out.append(
            _line(
                "Account snapshot",
                "FAIL",
                "no snapshot: Risk Guardian status UNKNOWN, READY blocked (Apex Account Health > Save snapshot)",
            )
        )
    else:
        snap = st.snapshot_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET") if st.snapshot_at else "?"
        out.append(
            _line(
                "Account snapshot",
                "FAIL" if st.stale else "PASS",
                f"{'STALE' if st.stale else 'fresh'} snapshot from {snap}; balance {st.balance}, risk budget "
                f"{st.risk_budget}" + (" - save a new one before trading" if st.stale else ""),
            )
        )
    rows = await load_rules(session)
    srcs = sorted({r.source_url for r in rows if r.verified and r.source_url})
    out.append(
        _line(
            "Apex rule profile",
            "PASS" if rules.verified else "WARN",
            (
                f"marked VERIFIED by an admin ({len(srcs)} source page(s)); this tool cannot confirm the values "
                "match Apex's current rules - re-check them on apextraderfunding.com"
            )
            if rules.verified
            else f"UNVERIFIED: {', '.join(rules.unverified_rules[:4])}...",
        )
    )
    # Research and gates
    for sym in symbols:
        stdy = await latest(session, sym)
        if stdy is None:
            out.append(
                _line(f"{sym} research", "WARN", "no research study stored: every setup shows WAIT (no evidence)")
            )
            continue
        counts: dict[str, int] = {}
        for x in stdy.result.get("rows", []):
            counts[x["evidence"]] = counts.get(x["evidence"], 0) + 1
        out.append(
            _line(
                f"{sym} research",
                "PASS",
                f"study #{stdy.id} ({stdy.data_class}, "
                f"{stdy.created_at.astimezone(ET):%Y-%m-%d %H:%M ET}); "
                f"contracts: {stdy.result.get('rollover', {}).get('contract_source', '?')}; evidence {counts}",
            )
        )
    out.append(
        _line(
            "Evidence gate",
            "PASS" if s.require_validated_strategy else "WARN",
            f"READY requires a validated strategy: {s.require_validated_strategy}; minimum net R:R to T1 "
            f"{s.min_net_reward_risk}",
        )
    )
    out.append(
        _line(
            "AI copilot",
            "PASS" if s.anthropic_api_key else "WARN",
            "API key configured" if s.anthropic_api_key else "not configured (optional)",
        )
    )
    out.append(_line("Order execution", "PASS", "analysis only: no broker order routing exists in this application"))
    ok = not any(x["result"] == "FAIL" for x in out)
    return {"checks": out, "ok": ok, "at": now.isoformat(), "ny_time": now.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z")}
