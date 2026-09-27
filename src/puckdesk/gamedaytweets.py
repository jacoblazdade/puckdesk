"""Game Day Tweets (gamedaytweets.com): beat-writer tweets about lines,
starting goalies and news, collected by @GameDayLines and @GameDayNewsNHL,
plus the site's starting-goalie guesses. Free to read, no X API needed.

Tweets appear as standard Twitter embeds (blockquote.twitter-tweet). The
goalie page is read as text ("Name (starter)"). `puckdesk verify-sources`
shows how many items each parser found on the live site.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

import httpx

from . import names
from .media import UA, strip_html
from .store import Store

BASE = "https://www.gamedaytweets.com"
TWITTER_EPOCH_MS = 1288834974657
RELAY_KIND = {"gamedaylines": "lines", "gamedaynewsnhl": "news", "gamedaystatsnhl": "stats"}
GOALIE_STATUS = r"(confirmed|starter|likely|probable|expected|unconfirmed|projected)"
log = logging.getLogger("puckdesk.gdt")

_BLOCK = re.compile(r'<blockquote[^>]*class="[^"]*twitter-tweet[^"]*"[^>]*>(.*?)</blockquote>', re.S | re.I)
_STATUS = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(\w+)/status/(\d+)", re.I)
_PROFILE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(\w+)(?![\w/]*status)[\"'/?]", re.I)


def tweet_time(tweet_id: str) -> datetime:
    """Tweet ids encode their creation time (snowflake)."""
    ms = (int(tweet_id) >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def parse_tweets(html_text: str) -> list[dict]:
    out, seen = [], set()
    for m in _BLOCK.finditer(html_text):
        block = m.group(1)
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S | re.I)
        text = strip_html(p.group(1) if p else block)
        st = _STATUS.search(block)
        if not st or not text:
            continue
        relay, tid = st.group(1), st.group(2)
        if tid in seen:
            continue
        seen.add(tid)
        # The original beat writer is linked just before the embed.
        before = html_text[max(0, m.start() - 800):m.start()]
        authors = [a for a in _PROFILE.findall(before) if a.lower() not in RELAY_KIND and a.lower() not in ("intent", "share", "home")]
        out.append(
            {
                "id": tid,
                "account": authors[-1] if authors else relay,
                "relay": relay,
                "kind": RELAY_KIND.get(relay.lower(), "news"),
                "posted_at": tweet_time(tid),
                "text": text,
                "url": f"https://x.com/{relay}/status/{tid}",
            }
        )
    return out


def parse_goalies(html_text: str) -> list[dict]:
    """Goalie guesses shown as 'Name (starter)' on the goalies page."""
    text = strip_html(html_text)
    out, seen = [], set()
    for m in re.finditer(r"([A-Z][\w'.\-]+(?:\s+[A-Z][\w'.\-]+){1,2})\s*\(" + GOALIE_STATUS + r"\)", text, re.I):
        name, status = m.group(1).strip(), m.group(2).lower()
        key = names.person(name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "status": status})
    return out


def sync(store: Store, pages: int = 3, game_date: date | None = None) -> dict:
    stored = 0
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as http:
        tweets: list[dict] = []
        for page in range(1, pages + 1):
            r = http.get(BASE + "/", params={"page": page} if page > 1 else None)
            r.raise_for_status()
            found = parse_tweets(r.text)
            if not found:
                break
            tweets += found
        r = http.get(BASE + "/goalies")
        r.raise_for_status()
        goalies = parse_goalies(r.text)

    with store.conn() as c:
        for t in tweets:
            row = c.execute(
                """insert into posts (id, source, account, kind, posted_at, text, url)
                   values (%s, 'gamedaytweets', %s, %s, %s, %s, %s) on conflict (id) do nothing returning id""",
                (t["id"], t["account"], t["kind"], t["posted_at"], t["text"], t["url"]),
            ).fetchone()
            stored += 1 if row else 0
        if game_date is None:
            from .engine import today_eastern

            game_date = today_eastern()
        for g in goalies:
            ref = store.find_player(g["name"], "", goalie=True)
            c.execute(
                """insert into goalie_guesses (game_date, norm_name, name, team, status) values (%s, %s, %s, %s, %s)
                   on conflict (game_date, norm_name) do update set status = excluded.status, team = excluded.team,
                     fetched_at = now()""",
                (game_date, names.person(g["name"]), g["name"], ref.team if ref else None, g["status"]),
            )
    return {"tweets_seen": len(tweets), "tweets_new": stored, "goalie_guesses": len(goalies)}


def verify() -> dict:
    out: dict = {}
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as http:
        home = http.get(BASE + "/").text
        tweets = parse_tweets(home)
        out["tweets"] = {"parsed": len(tweets), "sample": tweets[:2]}
        if not tweets:
            i = home.find("twitter.com")
            out["tweets"]["html_near_first_twitter_link"] = home[max(0, i - 600): i + 600] if i >= 0 else None
        gpage = http.get(BASE + "/goalies").text
        goalies = parse_goalies(gpage)
        out["goalies"] = {"parsed": len(goalies), "sample": goalies[:4]}
        if not goalies:
            out["goalies"]["text_sample"] = strip_html(gpage)[:1500]
    return out
