"""Podcasts and articles: RSS ingest, local transcription and full-text search.

Podcast audio is transcribed on the Mac mini with Whisper (mlx-whisper on
Apple Silicon), kept as ~1-minute windows with timestamps, and the audio file
is deleted afterwards. Articles come from RSS feeds with their full text.
"""

from __future__ import annotations

import html
import logging
import os
import re
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .store import Store

log = logging.getLogger("puckdesk.media")

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) puckdesk/0.1"}
WINDOW_SECONDS = 50


# --- configuration ------------------------------------------------------------------
def load_sources(path: str | os.PathLike | None = None) -> dict:
    p = Path(path or os.environ.get("PUCKDESK_SOURCES", "sources.toml"))
    if not p.exists():
        return {}
    return tomllib.loads(p.read_text())


# --- RSS ------------------------------------------------------------------------------
@dataclass
class Item:
    guid: str
    title: str
    link: str | None
    published: datetime | None
    audio_url: str | None = None
    duration_sec: int | None = None
    categories: list[str] = field(default_factory=list)
    content: str | None = None


def _text(el: ET.Element | None) -> str | None:
    return el.text.strip() if el is not None and el.text else None


def _duration(v: str | None) -> int | None:
    if not v:
        return None
    try:
        parts = [int(float(p)) for p in v.strip().split(":")]
    except ValueError:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def parse_rss(xml_text: str) -> list[Item]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        enclosure = it.find("enclosure")
        pub = _text(it.find("pubDate"))
        try:
            published = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            published = None
        guid = _text(it.find("guid")) or _text(it.find("link")) or _text(it.find("title")) or ""
        content = _text(it.find("content:encoded", NS)) or _text(it.find("description"))
        items.append(
            Item(
                guid=guid,
                title=_text(it.find("title")) or "(untitled)",
                link=_text(it.find("link")),
                published=published,
                audio_url=enclosure.get("url") if enclosure is not None else None,
                duration_sec=_duration(_text(it.find("itunes:duration", NS))),
                categories=[c.text.strip() for c in it.findall("category") if c.text],
                content=strip_html(content) if content else None,
            )
        )
    return items


def fetch(url: str) -> str:
    r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.text


# --- articles -------------------------------------------------------------------------
def sync_feed(store: Store, name: str, url: str) -> int:
    items = parse_rss(fetch(url))
    n = 0
    with store.conn() as c:
        for i in items:
            row = c.execute(
                """insert into articles (source, guid, title, link, published, categories, content)
                   values (%s, %s, %s, %s, %s, %s, %s)
                   on conflict (guid) do update set title = excluded.title, content = excluded.content,
                     categories = excluded.categories
                   returning (xmax = 0) as inserted""",
                (name, i.guid, i.title, i.link, i.published, i.categories, i.content),
            ).fetchone()
            n += 1 if row and row["inserted"] else 0
    return n


# --- podcasts ---------------------------------------------------------------------------
def sync_podcast(store: Store, name: str, feed: str, backfill_days: int = 21) -> int:
    """Record new episodes. Recent ones are queued for transcription, older ones skipped."""
    items = parse_rss(fetch(feed))
    cutoff = datetime.now(timezone.utc) - timedelta(days=backfill_days)
    n = 0
    with store.conn() as c:
        for i in items:
            status = "new" if (i.published and i.published >= cutoff and i.audio_url) else "skipped"
            row = c.execute(
                """insert into podcast_episodes (podcast, guid, title, published, duration_sec, audio_url, link, status)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (guid) do nothing returning id""",
                (name, i.guid, i.title, i.published, i.duration_sec, i.audio_url, i.link, status),
            ).fetchone()
            n += 1 if row else 0
    return n


def windows(segments: list[dict], seconds: float = WINDOW_SECONDS) -> list[dict]:
    """Merge Whisper's short segments into ~1-minute windows."""
    out: list[dict] = []
    cur: dict | None = None
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        if cur is None:
            cur = {"start": float(s["start"]), "end": float(s["end"]), "text": text}
        else:
            cur["end"] = float(s["end"])
            cur["text"] += " " + text
        if cur["end"] - cur["start"] >= seconds:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def transcribe(path: Path, prompt: str | None = None) -> list[dict]:
    """Segments [{start, end, text}] from the best local backend available."""
    model = os.environ.get("PUCKDESK_WHISPER_MODEL")
    try:
        import mlx_whisper  # Apple Silicon

        result = mlx_whisper.transcribe(
            str(path),
            path_or_hf_repo=model or "mlx-community/whisper-large-v3-turbo",
            initial_prompt=prompt,
            condition_on_previous_text=False,
        )
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in result["segments"]]
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel  # CPU fallback, slower

        m = WhisperModel(model or "small", device="cpu", compute_type="int8")
        segs, _ = m.transcribe(str(path), initial_prompt=prompt, vad_filter=True)
        return [{"start": s.start, "end": s.end, "text": s.text} for s in segs]
    except ImportError as e:
        raise RuntimeError("No Whisper backend installed: run `uv sync --extra mac` on the Mac mini.") from e


def download(url: str, dest: Path) -> Path:
    with httpx.stream("GET", url, headers=UA, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    return dest


def transcribe_pending(store: Store, limit: int = 2, prompt: str | None = None) -> list[dict]:
    with store.conn() as c:
        eps = c.execute(
            "select id, title, audio_url from podcast_episodes where status = 'new' order by published desc limit %s",
            (limit,),
        ).fetchall()
    done = []
    for ep in eps:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                audio = download(ep["audio_url"], Path(tmp) / "episode.mp3")
                segs = windows(transcribe(audio, prompt))
            except Exception as e:  # noqa: BLE001 - keep going with the next episode
                log.exception("transcription failed for %s", ep["title"])
                with store.conn() as c:
                    c.execute("update podcast_episodes set status = 'failed', error = %s where id = %s", (str(e)[:500], ep["id"]))
                continue
        with store.conn() as c, c.transaction():
            c.execute("delete from transcript_windows where episode_id = %s", (ep["id"],))
            with c.cursor() as cur:
                for i, w in enumerate(segs):
                    cur.execute(
                        "insert into transcript_windows (episode_id, idx, start_sec, end_sec, text) values (%s, %s, %s, %s, %s)",
                        (ep["id"], i, w["start"], w["end"], w["text"]),
                    )
            c.execute(
                "update podcast_episodes set status = 'transcribed', transcribed_at = now(), error = null where id = %s",
                (ep["id"],),
            )
        done.append({"title": ep["title"], "windows": len(segs)})
    return done


# --- search -----------------------------------------------------------------------------
def clock(sec: float | None) -> str | None:
    if sec is None:
        return None
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def search(store: Store, query: str, days: int = 14, limit: int = 12) -> list[dict]:
    """Full-text search over podcast transcripts, articles and posts, newest first."""
    head = "StartSel=**, StopSel=**, MaxFragments=2, MaxWords=35, MinWords=12, FragmentDelimiter= … "
    sql = f"""
    with q as (select websearch_to_tsquery('english', %(q)s) as query)
    select * from (
      select 'podcast' as kind, e.podcast as source, e.title, e.published as at, w.start_sec, e.id as ref,
             e.link, ts_headline('english', w.text, q.query, '{head}') as snippet, ts_rank(w.tsv, q.query) as rank
        from transcript_windows w join podcast_episodes e on e.id = w.episode_id, q
       where w.tsv @@ q.query and e.published >= now() - make_interval(days => %(days)s)
      union all
      select 'article', a.source, a.title, a.published, null, a.id, a.link,
             ts_headline('english', a.content, q.query, '{head}'), ts_rank(a.tsv, q.query)
        from articles a, q
       where a.tsv @@ q.query and a.published >= now() - make_interval(days => %(days)s)
      union all
      select 'post', p.account, left(p.text, 80), p.posted_at, null, null, p.url, p.text, ts_rank(p.tsv, q.query)
        from posts p, q
       where p.tsv @@ q.query and p.posted_at >= now() - make_interval(days => %(days)s)
    ) hits order by at desc nulls last, rank desc limit %(limit)s
    """
    with store.conn() as c:
        rows = c.execute(sql, {"q": query, "days": days, "limit": limit}).fetchall()
    return [
        {
            "kind": r["kind"],
            "source": r["source"],
            "title": r["title"],
            "at": r["at"].isoformat() if r["at"] else None,
            "timestamp": clock(r["start_sec"]),
            "episode_id": r["ref"] if r["kind"] == "podcast" else None,
            "link": r["link"],
            "snippet": r["snippet"],
        }
        for r in rows
    ]


def transcript(store: Store, episode_id: int, start_sec: float = 0, minutes: float = 5) -> dict:
    with store.conn() as c:
        ep = c.execute("select id, podcast, title, published, status from podcast_episodes where id = %s", (episode_id,)).fetchone()
        if not ep:
            return {"error": f"No episode {episode_id}."}
        rows = c.execute(
            """select start_sec, end_sec, text from transcript_windows
               where episode_id = %s and end_sec >= %s and start_sec <= %s order by idx""",
            (episode_id, start_sec, start_sec + minutes * 60),
        ).fetchall()
    return {
        "episode": ep["title"],
        "podcast": ep["podcast"],
        "published": ep["published"].isoformat() if ep["published"] else None,
        "status": ep["status"],
        "text": [{"at": clock(r["start_sec"]), "text": r["text"]} for r in rows],
    }


def episodes(store: Store, limit: int = 8) -> list[dict]:
    with store.conn() as c:
        rows = c.execute(
            """select e.id, e.podcast, e.title, e.published, e.duration_sec, e.status, e.error,
                      (select count(*) from transcript_windows w where w.episode_id = e.id) as windows
               from podcast_episodes e order by e.published desc nulls last limit %s""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"], "podcast": r["podcast"], "title": r["title"],
            "published": r["published"].isoformat() if r["published"] else None,
            "length": clock(r["duration_sec"]), "status": r["status"], "error": r["error"], "windows": r["windows"],
        }
        for r in rows
    ]


def recent_articles(store: Store, days: int = 3, limit: int = 20) -> list[dict]:
    with store.conn() as c:
        rows = c.execute(
            """select source, title, link, published, categories, left(content, 400) as preview from articles
               where published >= now() - make_interval(days => %s) order by published desc limit %s""",
            (days, limit),
        ).fetchall()
    return [
        {"source": r["source"], "title": r["title"], "link": r["link"],
         "published": r["published"].isoformat() if r["published"] else None,
         "categories": r["categories"], "preview": r["preview"]}
        for r in rows
    ]
