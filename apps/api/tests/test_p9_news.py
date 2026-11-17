"""News intelligence: parsing, classification, dedupe/clustering, ingestion, alerts, bias, API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.models.calendar import NewsItem
from app.models.signal import AlertRow
from app.research.bars import Bar
from app.services.news import SOURCE_BY_KEY, bias, classify, ingest, parse_rss, reaction, similar

NOW = datetime.now(UTC).replace(microsecond=0)


def rfc(d: datetime) -> str:
    return d.strftime("%a, %d %b %Y %H:%M:%S GMT")


def fed_xml(items: list[tuple[str, datetime]]) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link><![CDATA[https://www.federalreserve.gov/x{i}.htm]]></link>"
        f"<guid><![CDATA[https://www.federalreserve.gov/x{i}.htm]]></guid><description><![CDATA[{t}]]></description>"
        f"<category>Monetary Policy</category><pubDate><![CDATA[{rfc(d)}]]></pubDate></item>"
        for i, (t, d) in enumerate(items)
    )
    head = '<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel><title>FRB</title>'
    return f"{head}{body}</channel></rss>".encode()


EIA = b"""<?xml version="1.0" encoding="ISO-8859-1" ?><rss version="2.0"><channel><title>Today in Energy</title>
<item><title>Weekly Petroleum Status Report shows crude inventories fell</title>
<link>https://www.eia.gov/todayinenergy/detail.php?id=1</link>
<pubDate>Tue, 17 Nov 2026  09:00:00 EST</pubDate><description>Crude oil stocks.</description></item>
<item><title>Solar capacity additions in 2026</title><link>https://www.eia.gov/todayinenergy/detail.php?id=2</link>
<pubDate>Mon, 16 Nov 2026 09:00:00 EST</pubDate><description>Solar.</description></item>
<item><title></title><pubDate>Mon, 16 Nov 2026 09:00:00 EST</pubDate></item>
</channel></rss>"""


def test_classification_rules() -> None:
    assert classify("fed", "Federal Reserve issues FOMC statement")[0] == "CRITICAL"
    imp, inst, _ = classify("fed", "Minutes of the Federal Open Market Committee, October 27-28, 2026")
    assert imp == "HIGH" and "ES" in inst and "GC" in inst
    assert classify("fed", "Agencies extend comment period for proposed guidance")[0] == "LOW"
    assert classify("fed", "Board announces new officer appointments")[0] == "UNKNOWN"
    imp, inst, _ = classify("eia", "Weekly Petroleum Status Report shows crude inventories fell")
    assert imp == "HIGH" and inst == ["CL", "MCL"]
    assert classify("eia", "Solar capacity additions")[1] == []


def test_parse_rss_keeps_publication_time_and_skips_bad_items() -> None:
    items = parse_rss(SOURCE_BY_KEY["eia"], EIA)
    assert len(items) == 2  # untitled item skipped
    assert items[0]["published_at"] == datetime(2026, 11, 17, 14, 0, tzinfo=UTC)  # 09:00 EST
    assert items[0]["uid"].startswith("eia:") and items[0]["link"].startswith("https://")
    assert items[0]["impact"] == "HIGH" and items[0]["credibility"] == "official"


def test_similarity_for_clustering() -> None:
    assert similar("Federal Reserve issues FOMC statement", "Federal Reserve issues FOMC statement (corrected)")
    assert not similar("Federal Reserve issues FOMC statement", "Solar capacity additions in 2026")


def _transport(fed: bytes, eia_status: int = 500) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if "federalreserve" in req.url.host:
            return httpx.Response(200, content=fed)
        return httpx.Response(eia_status, content=EIA if eia_status == 200 else b"down")

    return httpx.MockTransport(handler)


async def test_ingest_dedupes_clusters_alerts_and_reports_source_errors() -> None:
    fed = fed_xml(
        [
            ("Federal Reserve issues FOMC statement", NOW - timedelta(minutes=5)),
            ("Federal Reserve issues FOMC statement (corrected)", NOW - timedelta(minutes=4)),
            ("Minutes of the Federal Open Market Committee", NOW - timedelta(days=3)),
            ("Agencies extend comment period", NOW - timedelta(hours=1)),
        ]
    )
    async with httpx.AsyncClient(transport=_transport(fed)) as client, get_sessionmaker()() as s:
        res = await ingest(s, get_redis(), client, NOW, ["fed", "eia"])
        await s.commit()
        assert res["new"] == 4
        assert res["sources"]["fed"]["ok"] is True and res["sources"]["eia"]["ok"] is False
        assert "HTTPStatusError" in res["sources"]["eia"]["error"]
        again = await ingest(s, get_redis(), client, NOW + timedelta(minutes=5), ["fed"])
        await s.commit()
        assert again["new"] == 0  # deduplicated by source guid
        rows = (await s.scalars(select(NewsItem).order_by(NewsItem.id))).all()
        assert rows[0].cluster == rows[1].cluster != rows[3].cluster
        assert all(r.detected_at == NOW for r in rows) and rows[0].published_at == NOW - timedelta(minutes=5)
        alerts = (await s.scalars(select(AlertRow).where(AlertRow.kind == "news"))).all()
        # One alert for the fresh FOMC cluster; the 3-day-old minutes are not "breaking".
        assert len(alerts) == 1 and "CRITICAL" in alerts[0].title


def _bars(start: datetime, n: int, step: float) -> list[Bar]:
    out, p = [], 5000.0
    for i in range(n):
        out.append(Bar(start + timedelta(minutes=i), p, p + 0.5, p - 0.5, p + step, 100))
        p += step
    return out


def test_reaction_and_bias() -> None:
    at = datetime(2026, 11, 11, 19, 0, tzinfo=UTC)
    bars = _bars(at - timedelta(hours=1), 60, 0.0) + [
        Bar(at + timedelta(minutes=i), 5000 + 2 * i, 5000 + 2 * i + 1, 5000 + 2 * i - 0.5, 5000 + 2 * i + 2, 100)
        for i in range(15)
    ]
    rx = reaction(bars, at)
    assert rx and rx["direction"] == "up" and rx["range_ratio"] > 1.5
    assert reaction(bars[:10], at) is None
    n = NewsItem(impact="CRITICAL", detected_at=at, published_at=at, instruments=["ES"], reaction=None)
    assert bias(n, at + timedelta(minutes=5))["bias"] == "NO TRADE"
    assert bias(n, at + timedelta(minutes=30))["bias"] == "WAIT"
    n.reaction = {"ES": rx}
    b = bias(n, at + timedelta(minutes=30), "ES")
    assert b["bias"] == "BUY" and "RESEARCH ONLY" in b["why"]
    low = NewsItem(impact="LOW", detected_at=at, published_at=at, instruments=["CL"], reaction=None)
    assert bias(low, at)["bias"] == "WAIT"


async def test_news_api_and_chart_markers(admin_client: httpx.AsyncClient) -> None:
    fed = fed_xml([("Federal Reserve issues FOMC statement", NOW - timedelta(minutes=30))])
    async with httpx.AsyncClient(transport=_transport(fed, 200)) as client, get_sessionmaker()() as s:
        await ingest(s, get_redis(), client, NOW, ["fed", "eia"])
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(NewsItem)) == 3
    r = (await admin_client.get("/api/news", params={"hours": 24 * 30})).json()
    titles = [i["title"] for i in r["items"]]
    assert "Federal Reserve issues FOMC statement" in titles
    fomc = next(i for i in r["items"] if i["impact"] == "CRITICAL")
    assert fomc["published_at"] != fomc["detected_at"] and fomc["bias"] in ("NO TRADE", "WAIT")
    assert "licensed provider" in r["breaking_feed"]
    assert {s["key"] for s in r["sources"]} == {"fed", "eia"}
    only_cl = (await admin_client.get("/api/news", params={"hours": 24 * 30, "symbol": "CL"})).json()["items"]
    assert only_cl and all("CL" in i["instruments"] for i in only_cl)
    get_settings().news_sources = []  # no network in tests
    refreshed = await admin_client.post("/api/news/refresh", headers={"X-Requested-With": "apex"})
    assert refreshed.status_code == 200 and refreshed.json()["new"] == 0


def test_old_items_found_on_first_sync_are_not_fresh() -> None:
    now = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)
    old = NewsItem(
        impact="CRITICAL",
        detected_at=now - timedelta(minutes=1),
        published_at=now - timedelta(days=30),
        instruments=["ES"],
        reaction=None,
    )
    assert bias(old, now)["bias"] == "WAIT"
    new = NewsItem(
        impact="CRITICAL",
        detected_at=now - timedelta(minutes=1),
        published_at=now - timedelta(minutes=3),
        instruments=["ES"],
        reaction=None,
    )
    assert bias(new, now)["bias"] == "NO TRADE"
