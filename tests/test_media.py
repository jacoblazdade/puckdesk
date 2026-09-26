"""Podcast/article ingest, search, and Daily Faceoff line parsing."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from puckdesk import lines, media
from puckdesk.store import Store

pgserver = pytest.importorskip("pgserver")

NOW = datetime.now(timezone.utc)


def rss_podcast():
    recent, old = format_datetime(NOW - timedelta(days=2)), format_datetime(NOW - timedelta(days=90))
    return f"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0"><channel>
<title>Keeping Karlsson Fantasy Hockey Podcast</title>
<item><title>No. 648 - Week 1 Waiver Wire</title><guid>ep-648</guid><pubDate>{recent}</pubDate>
<itunes:duration>01:52:10</itunes:duration><enclosure url="https://example.com/648.mp3" type="audio/mpeg"/></item>
<item><title>No. 600 - Old Episode</title><guid>ep-600</guid><pubDate>{old}</pubDate>
<itunes:duration>3600</itunes:duration><enclosure url="https://example.com/600.mp3" type="audio/mpeg"/></item>
</channel></rss>"""


def rss_articles():
    pub = format_datetime(NOW - timedelta(hours=5))
    return f"""<?xml version="1.0"?>
<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0"><channel><title>DobberHockey</title>
<item><title>Ramblings: Tolliver on the top unit</title><link>https://dobberhockey.com/r1</link><guid>d-1</guid>
<pubDate>{pub}</pubDate><category>ramblings</category>
<content:encoded><![CDATA[<p>Rhys Tolliver skated on <b>PP1</b> at practice today.</p><script>x()</script>]]></content:encoded></item>
</channel></rss>"""


@pytest.fixture(scope="module")
def store():
    srv = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
    s = Store(srv.get_uri())
    s.init()
    yield s
    srv.cleanup()


def test_parse_rss():
    items = media.parse_rss(rss_podcast())
    assert items[0].duration_sec == 6730 and items[0].audio_url.endswith("648.mp3")
    art = media.parse_rss(rss_articles())[0]
    assert "PP1 at practice" in art.content and "x()" not in art.content
    assert art.categories == ["ramblings"]


def test_windows_merge_segments():
    segs = [{"start": i * 10, "end": i * 10 + 10, "text": f"part {i}"} for i in range(12)]
    w = media.windows(segs, seconds=50)
    assert len(w) == 3 and w[0]["start"] == 0 and w[0]["end"] == 50
    assert w[-1]["text"] == "part 10 part 11"


def test_podcast_articles_and_search(store, monkeypatch):
    monkeypatch.setattr(media, "fetch", lambda url: rss_podcast() if "simplecast" in url else rss_articles())
    assert media.sync_podcast(store, "Keeping Karlsson", "https://feeds.simplecast.com/x", backfill_days=21) == 2
    assert media.sync_feed(store, "DobberHockey", "https://dobberhockey.com/feed/") == 1
    eps = media.episodes(store)
    assert {e["title"]: e["status"] for e in eps} == {"No. 648 - Week 1 Waiver Wire": "new", "No. 600 - Old Episode": "skipped"}

    # Pretend Whisper ran: download and transcribe are replaced.
    monkeypatch.setattr(media, "download", lambda url, dest: dest)
    monkeypatch.setattr(media, "transcribe", lambda path, prompt=None: [
        {"start": 0, "end": 30, "text": "Welcome back to Keeping Karlsson."},
        {"start": 30, "end": 60, "text": "Talking waivers."},
        {"start": 4350, "end": 4380, "text": "Tolliver is on the top power play in Seattle, go get him."},
        {"start": 4380, "end": 4410, "text": "Pick him up this week."},
    ])
    done = media.transcribe_pending(store, limit=2)
    assert done == [{"title": "No. 648 - Week 1 Waiver Wire", "windows": 2}]

    hits = media.search(store, '"Rhys Tolliver" OR "Tolliver"', days=10)
    kinds = {h["kind"] for h in hits}
    assert kinds == {"podcast", "article"}
    pod = next(h for h in hits if h["kind"] == "podcast")
    assert pod["timestamp"] == "1:12:30" and "**Tolliver**" in pod["snippet"]
    tr = media.transcript(store, pod["episode_id"], 4340, 2)
    assert tr["text"][0]["at"] == "1:12:30"


def next_data_page(rows, updated="2026-10-20T15:00:00Z"):
    data = {"props": {"pageProps": {"combinations": {"updatedAt": updated, "players": rows}}}}
    return f'<html><body><h1>Lines</h1><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def row(name, gid, gname, pos="lw", injury=None):
    return {"name": name, "groupIdentifier": gid, "groupName": gname, "positionIdentifier": pos, "injuryStatus": injury}


BASE = [
    row("A One", "f1", "1st Line"), row("A Two", "f1", "1st Line", "c"), row("A Three", "f1", "1st Line", "rw"),
    row("B One", "f2", "2nd Line"), row("B Two", "f2", "2nd Line", "c"), row("Rhys Tolliver", "f2", "2nd Line", "rw"),
    row("D One", "d1", "1st Defensive Pair", "ld"), row("D Two", "d1", "1st Defensive Pair", "rd"),
    row("A One", "pp1", "1st Powerplay Unit"), row("A Two", "pp1", "1st Powerplay Unit"),
    row("B One", "pp2", "2nd Powerplay Unit"), row("Rhys Tolliver", "pp2", "2nd Powerplay Unit"),
    row("Kai Moreau", "g1", "Goalies", "g"), row("Joey Backup", "g2", "Goalies", "g"),
    row("Hurt Guy", "ir", "Injuries", "c", "out"),
]


def test_parse_lines_and_diff():
    parsed = lines.parse_lines(next_data_page(BASE))
    assert parsed["source"] == "json"
    g = parsed["groups"]
    assert g["f2"] == ["B One", "B Two", "Rhys Tolliver"] and g["pp1"] == ["A One", "A Two"]
    assert g["goalies"] == ["Kai Moreau", "Joey Backup"]
    assert parsed["injuries"] == [{"name": "Hurt Guy", "status": "out"}]
    assert parsed["updated_at"].startswith("2026-10-20")

    promoted = [r for r in BASE if not (r["name"] == "Rhys Tolliver" and r["groupIdentifier"] == "pp2")]
    promoted.append(row("Rhys Tolliver", "pp1", "1st Powerplay Unit"))
    changes = lines.diff(parsed, lines.parse_lines(next_data_page(promoted)))
    assert {"player": "Rhys Tolliver", "field": "pp_unit", "old": "pp2", "new": "pp1"} in changes


def test_parse_lines_text_fallback():
    out = lines.parse_lines("<html><body><p>Line 1: A - B - C</p></body></html>")
    assert out["source"] == "text" and "Line 1" in out["text"]


def test_sync_team_records_changes(store):
    pages = [next_data_page(BASE)]
    promoted = [r for r in BASE if not (r["name"] == "Rhys Tolliver" and r["groupIdentifier"] == "pp2")]
    promoted.append(row("Rhys Tolliver", "pp1", "1st Powerplay Unit"))
    pages.append(next_data_page(promoted, updated="2026-10-21T15:00:00Z"))
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=pages[0])))
    assert lines.sync_team(store, "SEA", http)["changed"] is True
    again = lines.sync_team(store, "SEA", http)
    assert again["changed"] is False
    pages.pop(0)
    res = lines.sync_team(store, "SEA", http)
    assert res["changes"] >= 1
    ch = lines.recent_changes(store, days=1, teams=["SEA"])
    assert any(c["player"] == "Rhys Tolliver" and c["to"] == "pp1" for c in ch)
    assert lines.latest(store, "SEA")["groups"]["pp1"][-1] == "Rhys Tolliver"
