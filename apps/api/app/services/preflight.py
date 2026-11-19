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
            out.append(
                _line(
                    f"{sym} data",
                    "FAIL",
                    f"DATA UNAVAILABLE: {unavailable_cause(sym, prov, feed)} (never derived from another symbol)",
                )
            )
            continue
        age = (now - (row[0] + timedelta(minutes=1))).total_seconds()
        when = row[0].astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
        desc = f"last {row[1]} at {when} ({int(age)} s old, {str(row[2]).upper()})" + (
            f", contract {contract}" if contract else ""
        )
        out.append(await _symbol_spec(session, prov, sym, contract, feed))
        if not mk["open"]:
            out.append(_line(f"{sym} data", "WARN", f"market closed ({mk['reason']}); {desc}"))
        elif age <= LIVE_MAX_AGE_S and str(row[2]).lower() == "live":
            out.append(_line(f"{sym} data", "PASS", desc))
        else:
            out.append(_line(f"{sym} data", "FAIL", f"STALE or not live while the market is open: {desc}"))
    # News calendar: freshness comes from the last SUCCESSFUL download (source_sync_state), never from
    # event insert times (upserts keep the original created_at, which made a fresh calendar look old).
    from app.services import news_intel

    intel = await news_intel.evaluate(session, now, "ES")
    fr = intel["freshness"]
    ev = (
        await session.execute(
            select(func.count())
            .select_from(EconomicEvent)
            .where(EconomicEvent.ts >= now - timedelta(hours=1), EconomicEvent.ts <= now + timedelta(days=1))
        )
    ).scalar() or 0
    last = (
        datetime.fromisoformat(fr["last_success_at"]).astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
        if fr["last_success_at"]
        else "never"
    )
    if fr["stale"]:
        label = (
            "UNAVAILABLE" if not fr["last_success_at"] and intel["status"] == news_intel.STALE and not ev else "STALE"
        )
        out.append(
            _line(
                "News calendar",
                "FAIL",
                f"{label}: last successful download {last} (stale after {fr['stale_after_hours']} h); "
                f"news risk is treated as UNKNOWN and no setup can be confirmed"
                + (f"; last error: {fr['last_error']}" if fr["last_error"] else "")
                + (f"; next attempt {fr['next_attempt_at']}" if fr["next_attempt_at"] else ""),
            )
        )
    else:
        out.append(
            _line(
                "News calendar",
                "PASS",
                f"last successful download {last} ({fr['age_hours']} h ago); {ev} events in the next 24 h; "
                f"status now: {intel['status']} (scheduled calendar, not breaking news)",
            )
        )
    # Account and rules
    rules, st = await current_state(session, now)
    if not st.known:
        out.append(
            _line(
                "Account snapshot",
                "INFO",
                "no snapshot stored (optional: market analysis does not need it; history is kept)",
            )
        )
    else:
        snap = st.snapshot_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET") if st.snapshot_at else "?"
        out.append(
            _line(
                "Account snapshot",
                "INFO",
                f"{'stale' if st.stale else 'fresh'} snapshot from {snap}; balance {st.balance} "
                "(optional: not required for market analysis; history is kept)",
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
            "Evidence labels",
            "PASS",
            "technical setups are shown with their historical evidence label; unvalidated strategies are marked "
            f"RESEARCH / UNVALIDATED; minimum net R:R to TP1 {s.min_net_reward_risk}",
        )
    )
    out.append(
        _line(
            "AI copilot",
            "PASS" if s.anthropic_api_key else "INFO",
            "API key configured" if s.anthropic_api_key else "not configured (optional)",
        )
    )
    out.append(
        _line(
            "Analysis-only mode",
            "PASS" if s.automation_mode == "APEX_MANUAL" else "FAIL",
            "APEX_MANUAL: analysis, news and journal only; no orders, no autopilot"
            if s.automation_mode == "APEX_MANUAL"
            else f"{s.automation_mode} is set: this build is analysis only - set APEX_AUTOMATION_MODE=APEX_MANUAL",
        )
    )
    out.append(
        _line(
            "Phone notifications",
            "PASS" if s.notify_provider != "none" else "INFO",
            s.notify_provider if s.notify_provider != "none" else "not configured (APEX_NOTIFY_PROVIDER)",
        )
    )
    out.append(
        _line(
            "Order execution",
            "PASS",
            "no broker order routing is implemented; Apex accounts can never be traded automatically",
        )
    )
    out.extend(await _analysis_checks(session, symbols, now))
    ok = not any(x["result"] == "FAIL" for x in out)
    return {"checks": out, "ok": ok, "at": now.isoformat(), "ny_time": now.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z")}


EXPECTED_SPEC = {
    "ES": (0.25, 12.50, 50.0),
    "NQ": (0.25, 5.00, 20.0),
    "MNQ": (0.25, 0.50, 2.0),
    "MES": (0.25, 1.25, 5.0),
}


_CONTRACT_RE = r"^{root}[FGHJKMNQUVXZ]\d{{1,2}}$"


def unavailable_cause(sym: str, prov: str, feed: dict[str, Any]) -> str:
    """The actual reason an instrument has no data (never filled from another instrument)."""
    if prov == "none":
        return "no market-data provider is configured (APEX_MARKET_DATA_PROVIDER)"
    fs = feed.get("symbols") or []
    if fs and sym not in fs:
        return f"{sym} is not in the feed's symbols: add it to APEX_MARKET_DATA_SYMBOLS and restart the worker"
    if not feed:
        return "the worker's live feed is not reporting (is the worker running?)"
    parts = []
    if prov == "databento" and sym not in (feed.get("contracts") or {}):
        parts.append(
            f"the Databento live feed has not mapped {sym}.v.0 to a contract (check that your Databento "
            "subscription/entitlement covers it)"
        )
    err = feed.get("provider_last_error") or feed.get("last_error")
    if err:
        parts.append(f"last feed error: {err}")
    return "; ".join(parts) or f"no {sym} bars stored from '{prov}' yet"


async def _symbol_spec(
    session: AsyncSession, prov: str, sym: str, contract: str | None, feed: dict[str, Any]
) -> dict[str, str]:
    """Contract specification and proof that the bars come from this instrument's own feed.

    Equal prices across related contracts (MNQ/NQ, MES/ES) are legitimate - they track the same index -
    and are never a failure. Instead this verifies: the instrument's own Databento instrument_id and raw
    contract symbol (distinct from its sibling's), the front-month mapping against the quarterly
    calendar, the provider/data class of the stored bars, and bar timing.
    """
    import re

    from app.marketdata.instruments import INSTRUMENTS
    from app.research.rollover import front_contract

    inst = INSTRUMENTS.get(sym)
    if inst is None:
        return _line(f"{sym} contract", "FAIL", "unknown instrument")
    tick, tv, pv = float(inst.tick_size), float(inst.tick_value), float(inst.point_value)
    problems: list[str] = []
    notes: list[str] = []
    exp = EXPECTED_SPEC.get(sym)
    if exp and (tick, tv, pv) != exp:
        problems.append(f"spec {tick}/{tv}/{pv} differs from expected {exp}")
    cmap = feed.get("contracts") or {}
    mine = cmap.get(sym) or {}
    iid = mine.get("instrument_id")
    if contract and not re.match(_CONTRACT_RE.format(root=re.escape(sym)), contract):
        problems.append(f"feed contract {contract} is not a {sym} contract")
    if inst.sibling and mine:
        sib = cmap.get(inst.sibling) or {}
        if iid is not None and iid == sib.get("instrument_id"):
            problems.append(f"instrument_id {iid} is the same as {inst.sibling}'s: bars would not be {sym}'s own")
        if contract and contract == sib.get("contract"):
            problems.append(f"contract {contract} is the same as {inst.sibling}'s")
    if contract and re.match(_CONTRACT_RE.format(root=re.escape(sym)), contract):
        cal = front_contract(sym, datetime.now(UTC).astimezone(ET).date())
        if contract != cal:
            # Volume-based rolls (roll rule "v") can lead the calendar by a few days: informative only.
            notes.append(f"calendar front month would be {cal} (volume roll may differ near expiry)")
    # Stored bars: own provider and data class; flag only a true copy (prices AND volumes identical).
    row = (
        await session.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE quality = 'simulated') FROM "
                "(SELECT quality FROM candles WHERE provider=:p AND symbol=:s ORDER BY ts DESC LIMIT 120) x"
            ),
            {"p": prov, "s": sym},
        )
    ).first()
    if prov == "databento" and row and row[1]:
        problems.append(f"{row[1]} of the last {row[0]} stored bars are labelled simulated under provider databento")
    if inst.sibling:
        same = (
            await session.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE a.close = b.close AND a.high = b.high AND a.low = b.low "
                    "AND a.open = b.open AND a.volume = b.volume) "
                    "FROM (SELECT ts, open, high, low, close, volume FROM candles WHERE provider=:p AND symbol=:s "
                    "ORDER BY ts DESC LIMIT 120) a JOIN candles b ON b.provider=:p AND b.symbol=:o AND b.ts=a.ts"
                ),
                {"p": prov, "s": sym, "o": inst.sibling},
            )
        ).first()
        if same and same[0] >= 30 and same[1] == same[0]:
            problems.append(
                f"the last {same[0]} bars match {inst.sibling} in price AND volume: these look like copied "
                f"{inst.sibling} records, not {sym}'s own feed (equal prices alone would be normal)"
            )
    last_event = (feed.get("last_event") or {}).get(sym)
    desc = (
        f"tick {tick} = ${tv:.2f}, point ${pv:g}; contract {contract or 'not reported by the feed'}"
        + (f", instrument_id {iid}" if iid is not None else "")
        + (f"; last live bar {last_event}" if last_event else "")
        + (f"; {'; '.join(notes)}" if notes else "")
    )
    if problems:
        return _line(f"{sym} contract", "FAIL", "; ".join(problems) + f" ({desc})")
    ok = prov != "databento" or (contract and iid is not None)
    return _line(
        f"{sym} contract",
        "PASS" if ok else "WARN",
        desc + ("" if ok else "; the live feed has not reported this instrument's own instrument_id/contract yet"),
    )


async def _analysis_checks(session: AsyncSession, symbols: list[str], now: datetime) -> list[dict[str, str]]:
    """Migrations, the live analyzer (SMC + setup engine) per instrument, journal and the signal window."""
    from pathlib import Path

    from app.models.research import JournalTrade
    from app.services import analyzer

    out: list[dict[str, str]] = []
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = Path(__file__).resolve().parents[2] / "alembic.ini"  # noqa: ASYNC240 (path math, no I/O)
        head = ScriptDirectory.from_config(Config(str(ini))).get_current_head()
        cur = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        out.append(
            _line(
                "Migrations",
                "PASS" if cur == head else "FAIL",
                f"database {cur}, code {head}" + ("" if cur == head else " - run: alembic upgrade head"),
            )
        )
    except Exception as exc:  # report, never a forced pass
        out.append(_line("Migrations", "WARN", f"could not compare with the code's head: {type(exc).__name__}"))
    for sym in symbols:
        try:
            doc = await analyzer.snapshot(session, sym, now, use_cache=False)
        except Exception as exc:
            await session.rollback()
            out.append(_line(f"{sym} analysis", "FAIL", f"analyzer error: {type(exc).__name__}: {str(exc)[:120]}"))
            continue
        tfs = doc.get("timeframes") or {}
        ok_tfs = [k for k, v in tfs.items() if v.get("available")]
        sig = doc.get("signal") or {}
        conf = doc.get("confluence") or {}
        if not doc["data"].get("available"):
            out.append(_line(f"{sym} analysis", "FAIL", doc["data"].get("note") or "DATA UNAVAILABLE"))
            continue
        out.append(
            _line(
                f"{sym} analysis",
                "PASS" if len(ok_tfs) == 4 else "WARN",
                f"SMC active on {', '.join(ok_tfs) or 'no timeframe'}; bias {doc['bias']['label']}; setup "
                f"{sig.get('display_state') or sig.get('state')}; confluence {conf.get('score')}/5; data "
                f"{doc['data']['label']} ({doc['data'].get('confidence', 'n/a')})",
            )
        )
    n = (await session.execute(select(func.count()).select_from(JournalTrade))).scalar() or 0
    out.append(_line("Trading journal", "PASS", f"accessible; {n} recorded trade(s)"))
    out.append(
        _line(
            "Signal window",
            "INFO",
            "analysis and developing setups update all session; strategy triggers are evaluated 09:35-15:30 ET "
            "and confirmations also need the New York session (unchanged rules)",
        )
    )
    return out
