"""Which news actually mentions a player, and which players get news in a digest.

A text mentions a player when it has his full name, or his last name together
with his team (city, nickname, hashtag or abbreviation) in the same text.
A last name shared with another current NHL player (Logan and Devin Cooley)
is ambiguous when that player's team is in the text too, or both play for the
same team; then only the full name counts. Ranking tables (rank, name, team,
numbers) are skipped: being 56th in a list isn't news.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from . import names

TEAM_WORDS: dict[str, tuple[str, ...]] = {
    "ANA": ("Anaheim", "Ducks", "#FlyTogether"),
    "BOS": ("Boston", "Bruins", "#NHLBruins"),
    "BUF": ("Buffalo", "Sabres", "#LetsGoBuffalo"),
    "CGY": ("Calgary", "Flames", "#Flames"),
    "CAR": ("Carolina", "Hurricanes", "Canes", "#LetsGoCanes"),
    "CHI": ("Chicago", "Blackhawks", "#Blackhawks"),
    "COL": ("Colorado", "Avalanche", "Avs", "#GoAvsGo"),
    "CBJ": ("Columbus", "Blue Jackets", "#CBJ"),
    "DAL": ("Dallas", "Stars", "#TexasHockey"),
    "DET": ("Detroit", "Red Wings", "#LGRW", "#RedWings"),
    "EDM": ("Edmonton", "Oilers", "#LetsGoOilers"),
    "FLA": ("Florida", "Panthers", "#TimeToHunt"),
    "LAK": ("Los Angeles", "Kings", "#GoKingsGo"),
    "MIN": ("Minnesota", "Wild", "#mnwild"),
    "MTL": ("Montreal", "Canadiens", "Habs", "#GoHabsGo"),
    "NSH": ("Nashville", "Predators", "Preds", "#Smashville"),
    "NJD": ("New Jersey", "Devils", "#NJDevils"),
    "NYI": ("Islanders", "Isles", "#Isles"),
    "NYR": ("Rangers", "#NYR"),
    "OTT": ("Ottawa", "Senators", "Sens", "#GoSensGo"),
    "PHI": ("Philadelphia", "Flyers", "#LetsGoFlyers"),
    "PIT": ("Pittsburgh", "Penguins", "Pens", "#LetsGoPens"),
    "SJS": ("San Jose", "Sharks", "#SJSharks"),
    "SEA": ("Seattle", "Kraken", "#SeaKraken"),
    "STL": ("St. Louis", "St Louis", "Blues", "#stlblues"),
    "TBL": ("Tampa", "Lightning", "Bolts", "#GoBolts"),
    "TOR": ("Toronto", "Maple Leafs", "Leafs", "#LeafsForever"),
    "UTA": ("Utah", "Mammoth", "#TusksUp"),
    "VAN": ("Vancouver", "Canucks", "#Canucks"),
    "VGK": ("Vegas", "Golden Knights", "#VegasBorn"),
    "WSH": ("Washington", "Capitals", "Caps", "#ALLCAPS"),
    "WPG": ("Winnipeg", "Jets", "#GoJetsGo"),
}
# Last names that are everyday words; in lower-case transcripts they only count as full names.
COMMON_WORDS = {"power", "wood", "hall", "king", "little", "young", "white", "black", "brown", "green", "love", "day",
                "rust", "street", "bird", "cross", "best", "case", "chance", "english", "frost", "mercer", "stone"}

_TABLE_ROW = re.compile(r"^\s*\d{1,3}\.?\s+\D+?\s+[A-Z]{2,3}\b.*\d+\.\d")


def fold(text: str) -> str:
    """Accents off, punctuation inside names unified: 'Stützle' -> 'Stutzle', 'Pierre-Luc' -> 'Pierre Luc'."""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[\-‐-―]", " ", s).replace("’", "'")


def _has(text: str, phrase: str, case_sensitive: bool) -> bool:
    flags = 0 if case_sensitive else re.I
    if phrase.startswith("#"):
        return re.search(re.escape(phrase) + r"\b", text, re.I) is not None
    return re.search(r"(?<![\w#])" + re.escape(fold(phrase)) + r"(?!\w)", text, flags) is not None


def team_in(text: str, team: str, case_sensitive: bool = True) -> bool:
    t = names.team(team)
    if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text):  # abbreviation, upper case only
        return True
    return any(_has(text, w, case_sensitive) for w in TEAM_WORDS.get(t, ()))


def is_table(paragraph: str) -> bool:
    lines = [ln for ln in paragraph.replace("\xa0", " ").splitlines() if ln.strip()]
    rows = sum(1 for ln in lines if _TABLE_ROW.match(ln))
    return rows >= 2 or (rows >= 1 and len(lines) <= 2) or "Rating" in paragraph and "Player" in paragraph and rows >= 1


class Matcher:
    def __init__(self, last_name_teams: dict[str, list[str]] | None = None):
        self.teams = last_name_teams or {}

    def shared(self, name: str) -> bool:
        return len(self.teams.get(names.last(name), [])) > 1

    def ambiguous(self, text: str, name: str, team: str, case_sensitive: bool = True) -> bool:
        """Another player with this last name is on the same team, or his team is in the text."""
        others = list(self.teams.get(names.last(name), []))
        t = names.team(team)
        if t in others:
            others.remove(t)
        return any(o == t or team_in(text, o, case_sensitive) for o in others)

    def matches(self, text: str, name: str, team: str, case_sensitive: bool = True) -> bool:
        """Full name, or last name plus the player's team, in this text."""
        t = fold(text)
        full = " ".join(fold(name).replace(".", " ").split())
        flags = 0 if case_sensitive else re.I
        if re.search(r"(?<!\w)" + re.escape(full).replace(r"\ ", r"[\s.]+") + r"(?!\w)", t, flags | re.I):
            return True
        last = fold(name).split()[-1] if name.split() else ""
        if not last:
            return False
        if not case_sensitive and last.lower() in COMMON_WORDS:
            return False
        if re.search(r"(?<!\w)" + re.escape(last) + r"(?!\w)", t, flags) is None:
            return False
        return team_in(t, team, case_sensitive) and not self.ambiguous(t, name, team, case_sensitive)


def paragraphs(text: str, kind: str) -> list[str]:
    if kind != "article":
        return [text]
    parts = re.split(r"\n\s*(?:\*\s*)?\n", text.replace("\xa0", " "))
    return [p.strip() for p in parts if p.strip()]


def snippet(paragraph: str, name: str, width: int = 300) -> str:
    p = " ".join(paragraph.split())
    if len(p) <= width:
        return p
    i = fold(p).lower().find(fold(name).split()[-1].lower())
    start = max(0, min(i - width // 3, len(p) - width)) if i >= 0 else 0
    return ("..." if start else "") + p[start:start + width].strip() + ("..." if start + width < len(p) else "")


def find(store, matcher: Matcher, name: str, team: str, days: int = 7) -> dict | None:
    """The newest item that really mentions the player, outside ranking tables."""
    query = f'"{name}" OR {names.last(name)}'
    for row in store.media_texts(query, days=days, limit=40):
        kind = row["kind"]
        for para in paragraphs(row["text"] or "", kind):
            if is_table(para):
                continue
            if matcher.matches(para, name, team, case_sensitive=kind != "podcast"):
                return {
                    "kind": kind,
                    "source": row["source"],
                    "title": row["title"],
                    "at": row["at"].isoformat() if isinstance(row["at"], datetime) else row["at"],
                    "timestamp": _clock(row.get("start_sec")),
                    "episode_id": row["ref"] if kind == "podcast" else None,
                    "link": row["link"],
                    "snippet": snippet(para, name),
                }
    return None


def _clock(sec: float | None) -> str | None:
    if sec is None:
        return None
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# --- Game Day Tweets lineup posts: is a player on the top power-play unit? ---------
_PP1 = re.compile(r"\bPP\s*1\b|\bPP1\b|top (?:power[ -]?play )?unit|top PP|first unit|1st unit|top power[ -]?play", re.I)
_PP2 = re.compile(r"\bPP\s*2\b|\bPP2\b|second unit|2nd unit", re.I)


def on_top_unit(text: str, matcher: Matcher, name: str, team: str) -> bool:
    """A lineup post puts the player on PP1: in a 'PP1' block, or on a line about the top unit."""
    if not matcher.matches(text, name, team):
        return False
    in_pp1 = False
    for line in text.splitlines():
        if not line.strip():
            in_pp1 = False
            continue
        if _PP2.search(line):
            in_pp1 = False
            continue
        header = _PP1.search(line)
        if header:
            in_pp1 = True
        if in_pp1 and _named(line, name, matcher):
            return True
    return False


def _named(line: str, name: str, matcher: Matcher) -> bool:
    """The line names him; the whole post already passed matches(), so team and ambiguity are settled."""
    t = fold(line)
    if fold(name).lower() in t.lower():
        return True
    last = fold(name).split()[-1]
    return re.search(r"(?<!\w)" + re.escape(last) + r"(?!\w)", t) is not None


# --- which players get news in a digest ------------------------------------------------
def digest_news(store, eng, dg: dict, limit: int = 8, days: int = 7) -> dict:
    """Lineup changes for players that matter, and the newest real mention for at most `limit` players:
    the moves, sell_high, breakout_watch, players with role changes, then injured players of mine."""
    from . import lines

    matcher = eng.valuer.matcher
    teams = {p.key: p.team for p in eng.me + eng.opp}
    teams.update({names.person(fa.name): names.team(fa.team) for fa in eng.state.free_agents})
    moves = dg["moves"]["moves"]
    interest = set(teams)
    my_teams = sorted({p.team for p in eng.me} | {m["add"]["team"] for m in moves})
    changes = [c for c in lines.recent_changes(store, days=2, teams=my_teams) if names.person(c["player"]) in interest]

    order: list[tuple[str, str]] = []
    for m in moves:
        order += [(m["add"]["name"], m["add"]["team"]), (m["drop"]["name"], m["drop"]["team"])]
    order += [(r["name"], r["team"]) for r in dg.get("sell_high", [])]
    order += [(r["name"], r["team"]) for r in dg.get("breakout_watch", [])]
    order += [(c["player"], c["team"]) for c in changes]
    order += [(p.name, p.team) for p in eng.me if (p.source.status or "").strip()]

    found: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for name, team in order:
        key = names.person(name)
        if key in seen or len(found) >= limit:
            continue
        seen.add(key)
        hit = find(store, matcher, name, team, days=days)
        if hit:
            found[name] = [hit]
    return {"lineup_changes": changes, "mentions": found}
