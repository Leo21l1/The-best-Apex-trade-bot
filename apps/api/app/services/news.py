"""News intelligence from authorised public sources (official RSS feeds). Nothing is fabricated.

* Sources are public official feeds (Federal Reserve press releases, EIA). Breaking-news wires
  need a licensed provider; none is configured, and the UI says so.
* Each item keeps the source's publication time and APEX's detection time separately. Reviews and
  replays may use an item only from ``detected_at`` onward.
* Impact is a deterministic keyword classification of relevance (LOW/MODERATE/HIGH/CRITICAL/
  UNKNOWN), not a forecast. Directional bias is never read from headline text: it is NO TRADE
  while a HIGH/CRITICAL item is fresh, then follows the OBSERVED price reaction only when that
  reaction is large, labelled RESEARCH ONLY; otherwise WAIT.
"""

from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from defusedxml import ElementTree
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import NewsItem
from app.research.bars import Bar

EQUITY = ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K"]
RATES_SENSITIVE = [*EQUITY, "GC", "MGC"]
ENERGY = ["CL", "MCL"]


@dataclass(frozen=True, slots=True)
class FeedSource:
    key: str
    name: str
    url: str
    credibility: str = "official"


SOURCES: tuple[FeedSource, ...] = (
    FeedSource("fed", "Federal Reserve Board press releases", "https://www.federalreserve.gov/feeds/press_all.xml"),
    FeedSource(
        "eia", "U.S. Energy Information Administration (Today in Energy)", "https://www.eia.gov/rss/todayinenergy.xml"
    ),
)
SOURCE_BY_KEY = {s.key: s for s in SOURCES}

# (pattern, impact, instruments, reason) - first match wins, evaluated per source.
RULES: dict[str, list[tuple[str, str, list[str], str]]] = {
    "fed": [
        (
            r"\bFOMC statement\b|federal open market committee statement|federal funds rate|"
            r"issues? FOMC|monetary policy statement",
            "CRITICAL",
            RATES_SENSITIVE,
            "FOMC policy decision",
        ),
        (
            r"\bminutes\b.*\b(FOMC|federal open market committee)\b|"
            r"\b(FOMC|federal open market committee)\b.*\bminutes\b",
            "HIGH",
            RATES_SENSITIVE,
            "FOMC minutes",
        ),
        (
            r"\bdiscount rate\b|\binterest on reserve|\bbalance sheet\b|\bemergency\b",
            "HIGH",
            RATES_SENSITIVE,
            "Policy-rate or balance-sheet action",
        ),
        (
            r"\bmonetary policy\b|\bsummary of economic projections\b|\bbeige book\b",
            "MODERATE",
            RATES_SENSITIVE,
            "Monetary-policy publication",
        ),
        (r"\bstress test\b|\bcapital\b|\bliquidity\b", "MODERATE", EQUITY, "Bank capital/liquidity policy"),
        (
            r"\benforcement\b|\bterminat|\bcomment period\b|\bapplication\b|\bpricing\b|\bguidance\b|\bproposal\b",
            "LOW",
            [],
            "Routine regulatory or administrative release",
        ),
    ],
    "eia": [
        (r"\bweekly petroleum status\b|\bcrude (oil )?inventor", "HIGH", ENERGY, "Crude inventory report"),
        (r"\bshort-term energy outlook\b|\bSTEO\b", "MODERATE", ENERGY, "EIA outlook publication"),
        (
            r"\bcrude\b|\boil\b|\bgasoline\b|\bdiesel\b|\bpetroleum\b|\bOPEC\b|\brefiner",
            "LOW",
            ENERGY,
            "Oil-market analysis article",
        ),
        (r".", "LOW", [], "Energy analysis article without a direct crude-oil link"),
    ],
}


def classify(source: str, title: str, summary: str = "") -> tuple[str, list[str], str]:
    text = f"{title} {summary}"
    for pat, impact, inst, reason in RULES.get(source, []):
        if re.search(pat, text, re.IGNORECASE):
            return impact, inst, reason
    return "UNKNOWN", [], "No classification rule matched; impact unknown."


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 3}


def similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.6


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s.strip())
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        # RFC 822 with a US zone abbreviation that email.utils does not map (e.g. "EST").
        m = re.match(r"(.*\d{2}:\d{2}(?::\d{2})?) (EST|EDT|CST|CDT|PST|PDT)$", s)
        if not m:
            return None
        offset = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "PST": -8, "PDT": -7}[m.group(2)]
        try:
            d = email.utils.parsedate_to_datetime(f"{m.group(1)} {offset:+03d}00")
        except (TypeError, ValueError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def parse_rss(source: FeedSource, xml: bytes) -> list[dict[str, Any]]:
    """Items from an RSS 2.0 document. Items without a title or a parseable date are skipped."""
    root = ElementTree.fromstring(xml)
    out: list[dict[str, Any]] = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        published = _date(it.findtext("pubDate"))
        if not title or published is None:
            continue
        link = (it.findtext("link") or "").strip() or None
        guid = (it.findtext("guid") or link or title).strip()
        summary = re.sub(r"<[^>]+>", " ", it.findtext("description") or "").strip()[:2000] or None
        cats = [c.text.strip() for c in it.findall("category") if c.text]
        impact, inst, reason = classify(source.key, title, summary or "")
        out.append(
            {
                "uid": f"{source.key}:{guid}"[:500],
                "source": source.key,
                "source_name": source.name,
                "credibility": source.credibility,
                "title": title[:500],
                "link": link if link and link.startswith(("http://", "https://")) else None,
                "summary": summary,
                "categories": cats,
                "published_at": published,
                "impact": impact,
                "impact_reason": reason,
                "instruments": inst,
            }
        )
    return out


async def fetch(source: FeedSource, client: httpx.AsyncClient) -> bytes:
    r = await client.get(source.url, headers={"User-Agent": "apex-research/1.0 (official feed reader)"}, timeout=15)
    r.raise_for_status()
    return r.content


async def store(session: AsyncSession, items: list[dict[str, Any]], now: datetime) -> list[NewsItem]:
    """Insert unseen items (dedupe by uid), assign clusters (similar titles within 6h). Returns new rows."""
    new: list[NewsItem] = []
    for item in items:
        res = await session.execute(
            insert(NewsItem)
            .values(**item, detected_at=now, cluster=item["uid"], reaction=None)
            .on_conflict_do_nothing(index_elements=["uid"])
            .returning(NewsItem.id)
        )
        row_id = res.scalar_one_or_none()
        if row_id is None:
            continue
        row = await session.get(NewsItem, row_id)
        assert row is not None
        window = row.published_at - timedelta(hours=6), row.published_at + timedelta(hours=6)
        near = (
            await session.scalars(
                select(NewsItem)
                .where(NewsItem.published_at.between(*window), NewsItem.id != row.id)
                .order_by(NewsItem.id)
            )
        ).all()
        match = next((n for n in near if similar(n.title, row.title)), None)
        if match is not None:
            row.cluster = match.cluster
        new.append(row)
    await session.flush()
    return new


def reaction(bars: list[Bar], at: datetime, minutes: int = 15) -> dict[str, Any] | None:
    """Observed move in the ``minutes`` after ``at`` versus the range of the prior hour (1m bars)."""
    before = [b for b in bars if at - timedelta(hours=1) <= b.ts < at]
    after = [b for b in bars if at <= b.ts < at + timedelta(minutes=minutes)]
    if len(before) < 20 or len(after) < minutes // 2:
        return None
    ref = before[-1].close
    hi, lo = max(b.high for b in after), min(b.low for b in after)
    prior = max(b.high for b in before) - min(b.low for b in before)
    change = after[-1].close - ref
    ratio = (hi - lo) / prior if prior > 0 else None
    return {
        "minutes": minutes,
        "change": round(change, 6),
        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
        "range_ratio": round(ratio, 2) if ratio is not None else None,
        "measured_to": after[-1].ts.isoformat(),
    }


FRESH_DETECTED = timedelta(minutes=15)
FRESH_PUBLISHED = timedelta(hours=2)


def is_fresh(item: NewsItem, now: datetime) -> bool:
    """Detected in the last 15 minutes AND published in the last 2 hours.

    An old release found on a first sync is not breaking news, whatever its detection time.
    """
    return now - item.detected_at < FRESH_DETECTED and now - item.published_at < FRESH_PUBLISHED


def bias(item: NewsItem, now: datetime, symbol: str | None = None) -> dict[str, str]:
    """BUY / SHORT / WAIT / NO TRADE for an item, never inferred from wording."""
    hot = item.impact in ("HIGH", "CRITICAL")
    if hot and is_fresh(item, now):
        return {"bias": "NO TRADE", "why": f"{item.impact} release detected less than 15 minutes ago; let it settle."}
    rx = (item.reaction or {}).get(symbol or (item.instruments[0] if item.instruments else ""), None)
    if hot and rx and (rx.get("range_ratio") or 0) >= 1.5 and rx["direction"] != "flat":
        side = "BUY" if rx["direction"] == "up" else "SHORT"
        return {
            "bias": side,
            "why": f"Follows the observed {rx['minutes']}-minute reaction ({rx['direction']}, "
            f"{rx['range_ratio']}x prior-hour range). Reaction-following is RESEARCH ONLY, not validated.",
        }
    return {"bias": "WAIT", "why": "No directional evidence: headlines are not interpreted for direction."}


def as_dict(n: NewsItem, now: datetime) -> dict[str, Any]:
    return {
        "id": n.id,
        "source": n.source,
        "source_name": n.source_name,
        "credibility": n.credibility,
        "title": n.title,
        "link": n.link,
        "summary": n.summary,
        "categories": n.categories,
        "published_at": n.published_at.isoformat(),
        "detected_at": n.detected_at.isoformat(),
        "impact": n.impact,
        "impact_reason": n.impact_reason,
        "instruments": n.instruments,
        "cluster": n.cluster,
        "reaction": n.reaction,
        **bias(n, now),
    }


STATUS_KEY = "apex:news:status"


async def ingest(
    session: AsyncSession, redis, client: httpx.AsyncClient, now: datetime, sources: list[str]
) -> dict[str, Any]:
    """Fetch each enabled source, store new items, alert on HIGH/CRITICAL, measure reactions."""
    import json

    from redis.exceptions import RedisError

    from app.services.alerts import emit_alert

    status: dict[str, Any] = {}
    try:
        raw = await redis.get(STATUS_KEY)
        status = json.loads(raw) if raw else {}
    except (RedisError, OSError, ValueError):
        status = {}
    created: list[NewsItem] = []
    for key in sources:
        src = SOURCE_BY_KEY.get(key)
        if src is None:
            continue
        st = status.get(key, {})
        try:
            items = parse_rss(src, await fetch(src, client))
            new = await store(session, items, now)
            created += new
            st.update({"ok": True, "last_ok": now.isoformat(), "items": len(items), "new": len(new), "error": None})
        except (httpx.HTTPError, OSError, ElementTree.ParseError, ValueError) as exc:
            st.update({"ok": False, "last_error_at": now.isoformat(), "error": f"{type(exc).__name__}: {exc}"[:200]})
        st.update({"name": src.name, "url": src.url, "credibility": src.credibility})
        status[key] = st
    for n in created:
        # Only alert on items published recently: a first sync must not replay old releases as "breaking".
        if n.impact in ("HIGH", "CRITICAL") and now - n.published_at <= timedelta(hours=2):
            await emit_alert(
                session,
                redis,
                kind="news",
                severity="warning" if n.impact == "HIGH" else "critical",
                title=f"{n.impact} news: {n.title[:120]}",
                body=f"{n.source_name}. {n.impact_reason}. "
                f"Published {n.published_at:%H:%M} UTC, detected {n.detected_at:%H:%M} UTC. Directional bias: NO TRADE "
                "until the release settles.",
                dedupe_key=f"news:{n.cluster}",
                cooldown_s=6 * 3600,
                data={"news_id": n.id, "instruments": n.instruments},
            )
    await measure_reactions(session, now)
    try:
        await redis.set(STATUS_KEY, json.dumps(status))
    except (RedisError, OSError):
        pass
    return {"sources": status, "new": len(created)}


async def measure_reactions(session: AsyncSession, now: datetime) -> int:
    """Fill ``reaction`` for HIGH/CRITICAL items detected 15 min - 6 h ago (from stored 1m bars)."""
    from app.core.config import get_settings
    from app.research.scanner import load_bars

    provider = get_settings().market_data_provider
    if provider == "none":
        return 0
    rows = (
        await session.scalars(
            select(NewsItem).where(
                NewsItem.impact.in_(("HIGH", "CRITICAL")),
                NewsItem.reaction.is_(None),
                NewsItem.detected_at <= now - timedelta(minutes=15),
                NewsItem.detected_at >= now - timedelta(hours=6),
            )
        )
    ).all()
    done = 0
    for n in rows:
        # The reaction is measured from detection: APEX could not have acted before it saw the item.
        # Items discovered long after publication (e.g. on a first sync) have no actionable reaction.
        if n.detected_at - n.published_at > FRESH_PUBLISHED:
            n.reaction = {}
            continue
        out = {}
        for sym in n.instruments[:4]:
            bars = await load_bars(
                session, provider, sym, n.detected_at - timedelta(hours=1), n.detected_at + timedelta(minutes=16)
            )
            rx = reaction(bars, n.detected_at)
            if rx:
                out[sym] = rx
        if out:
            n.reaction = out
            done += 1
    return done
