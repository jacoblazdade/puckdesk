"""Game Day Tweets (gamedaytweets.com): beat-writer tweets about lines,
starting goalies, stats and news, relayed by @GameDayLines, @GameDayNewsNHL,
@GameDayStatsNHL and @GameDayGoalies, plus the site's starting-goalie
guesses. Free to read, no X API needed.

The home page renders each post as <blockquote class="tweet ...">: the
original account is the a.handle link (missing on the relay's own posts) and
the x.com/<relay>/status/<id> link gives relay and id. Standard Twitter
embeds (blockquote.twitter-tweet, beat writer linked just before) are still
accepted. The goalie page is read as text: a date heading, then
"Our Guess: Name (reason)" per team. `puckdesk verify-sources` shows how many
items each parser found on the live site.
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
RELAY_KIND = {"gamedaylines": "lines", "gamedaynewsnhl": "news", "gamedaystatsnhl": "stats", "gamedaygoalies": "goalies"}
GOALIE_STATUS = r"(confirmed|starter|likely|probable|expected|unconfirmed|projected)"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
log = logging.getLogger("puckdesk.gdt")

_BLOCK = re.compile(r'<blockquote[^>]*class="([^"]*)"[^>]*>(.*?)</blockquote>', re.S | re.I)
_HANDLE = re.compile(r'<a[^>]*class="[^"]*\bhandle\b[^"]*"[^>]*>\s*@?(\w+)\s*</a>', re.S | re.I)
_GUESS = re.compile(r"Our\s+Guess\s*:\s*([^\n(]+?)\s*\(([^)\n]+)\)", re.I)
_DATE_HEADING = re.compile(r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_STATUS = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(\w+)/status/(\d+)", re.I)
_PROFILE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(\w+)(?![\w/]*status)[\"'/?]", re.I)


def tweet_time(tweet_id: str) -> datetime:
    """Tweet ids encode their creation time (snowflake)."""
    ms = (int(tweet_id) >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def parse_tweets(html_text: str) -> list[dict]:
    out, seen = [], set()
    for m in _BLOCK.finditer(html_text):
        classes, block = m.group(1).lower().split(), m.group(2)
        if "tweet" not in classes and "twitter-tweet" not in classes:
            continue
        embed = "twitter-tweet" in classes
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S | re.I)
        body = p.group(1) if p else block
        handle = None if embed else _HANDLE.search(body)
        if handle:
            body = body[: handle.start()] + body[handle.end():]
        text = strip_html(body)
        st = _STATUS.search(block)
        if not st or not text:
            continue
        relay, tid = st.group(1), st.group(2)
        if tid in seen:
            continue
        seen.add(tid)
        if embed:
            # Standard embed: the original beat writer is linked just before it.
            before = html_text[max(0, m.start() - 800):m.start()]
            authors = [a for a in _PROFILE.findall(before) if a.lower() not in RELAY_KIND and a.lower() not in ("intent", "share", "home")]
            account = authors[-1] if authors else relay
        else:
            # Site layout: a.handle names the account; without one it's the relay's own post.
            account = handle.group(1) if handle else relay
        out.append(
            {
                "id": tid,
                "account": account,
                "relay": relay,
                "kind": RELAY_KIND.get(relay.lower(), "news"),
                "posted_at": tweet_time(tid),
                "text": text,
                "url": f"https://x.com/{relay}/status/{tid}",
            }
        )
    return out


def parse_goalies(html_text: str) -> list[dict]:
    """Goalie guesses: 'Our Guess: Name (reason)', the reason kept as the status.

    Embedded tweets on the page also hold names and brackets, so only
    "Our Guess:" lines count. Older pages without them fall back to 'Name (starter)'.
    """
    text = strip_html(html_text)
    found = [(m.group(1), m.group(2)) for m in _GUESS.finditer(text)]
    if not found:
        found = [(m.group(1), m.group(2)) for m in re.finditer(r"([A-Z][\w'.\-]+(?:\s+[A-Z][\w'.\-]+){1,2})\s*\(" + GOALIE_STATUS + r"\)", text, re.I)]
    out, seen = [], set()
    for name, status in found:
        name, status = " ".join(name.split()), " ".join(status.split()).lower()
        key = names.person(name)
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "status": status})
    return out


def page_date(html_text: str, today: date | None = None) -> date | None:
    """The goalie page's date heading ('Saturday, October 3rd'), in the year nearest to today."""
    m = _DATE_HEADING.search(strip_html(html_text))
    if not m:
        return None
    if today is None:
        from .engine import today_eastern

        today = today_eastern()
    month, day = MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:  # 29 February
            pass
    return min(options, key=lambda d: abs((d - today).days)) if options else None


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
        if game_date is None:
            game_date = page_date(r.text)

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
    return {"tweets_seen": len(tweets), "tweets_new": stored, "goalie_guesses": len(goalies), "goalie_date": game_date}


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
        out["goalies"] = {"parsed": len(goalies), "date": page_date(gpage), "sample": goalies[:4]}
        if not goalies:
            out["goalies"]["text_sample"] = strip_html(gpage)[:1500]
    return out
