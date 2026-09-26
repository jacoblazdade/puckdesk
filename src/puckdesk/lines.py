"""Daily Faceoff line combinations: lines, PP units, goalies and injuries per
team, plus change detection (who moved up to PP1 or into the top six).

The team pages are server-rendered by Next.js, so the data sits in the page's
__NEXT_DATA__ JSON. Its exact field names aren't documented; the parser looks
for the list of player rows by shape, and `puckdesk verify-sources` shows what
it found on the live site.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from . import names
from .media import UA, strip_html
from .store import Store

log = logging.getLogger("puckdesk.lines")

TEAM_SLUGS = {
    "ANA": "anaheim-ducks", "BOS": "boston-bruins", "BUF": "buffalo-sabres", "CGY": "calgary-flames",
    "CAR": "carolina-hurricanes", "CHI": "chicago-blackhawks", "COL": "colorado-avalanche",
    "CBJ": "columbus-blue-jackets", "DAL": "dallas-stars", "DET": "detroit-red-wings",
    "EDM": "edmonton-oilers", "FLA": "florida-panthers", "LAK": "los-angeles-kings",
    "MIN": "minnesota-wild", "MTL": "montreal-canadiens", "NSH": "nashville-predators",
    "NJD": "new-jersey-devils", "NYI": "new-york-islanders", "NYR": "new-york-rangers",
    "OTT": "ottawa-senators", "PHI": "philadelphia-flyers", "PIT": "pittsburgh-penguins",
    "SJS": "san-jose-sharks", "SEA": "seattle-kraken", "STL": "st-louis-blues",
    "TBL": "tampa-bay-lightning", "TOR": "toronto-maple-leafs", "UTA": "utah-mammoth",
    "VAN": "vancouver-canucks", "VGK": "vegas-golden-knights", "WSH": "washington-capitals",
    "WPG": "winnipeg-jets",
}
URL = "https://www.dailyfaceoff.com/teams/{slug}/line-combinations"

NAME_KEYS = ("name", "playerName", "fullName", "player_name")
GROUP_ID_KEYS = ("groupIdentifier", "group_identifier", "lineIdentifier", "groupId")
GROUP_NAME_KEYS = ("groupName", "group_name", "lineName", "categoryName")
POS_KEYS = ("positionIdentifier", "position", "positionName")
INJURY_KEYS = ("injuryStatus", "injury_status", "status")
UPDATED_KEYS = ("updatedAt", "lastUpdated", "updated_at", "lastUpdatedAt")
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def _get(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def next_data(html_text: str) -> dict | None:
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _walk(obj: Any, path: str = "") -> Any:
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def find_player_rows(data: Any) -> tuple[str | None, list[dict]]:
    """The largest list of dicts that carry a player name and a line/group label."""
    best: tuple[str | None, list[dict]] = (None, [])
    for path, v in _walk(data):
        if isinstance(v, list) and len(v) >= 8 and all(isinstance(x, dict) for x in v):
            good = [x for x in v if _get(x, NAME_KEYS) and (_get(x, GROUP_ID_KEYS) or _get(x, GROUP_NAME_KEYS))]
            if len(good) >= 8 and len(good) > len(best[1]):
                best = (path, good)
    return best


def find_updated(data: Any) -> str | None:
    for _, v in _walk(data):
        if isinstance(v, dict):
            u = _get(v, UPDATED_KEYS)
            if isinstance(u, str) and re.match(r"\d{4}-\d{2}-\d{2}", u):
                return u
    return None


def role(group_id: str | None, group_name: str | None) -> str:
    s = f"{group_id or ''} {group_name or ''}".lower()
    n = None
    m = re.search(r"(\d)", s)
    if m:
        n = int(m.group(1))
    for word, v in ORDINALS.items():
        if word in s:
            n = v
    if "power" in s or re.search(r"\bpp", s):
        return f"pp{n or 1}"
    if "penalty" in s or re.search(r"\bpk", s):
        return f"pk{n or 1}"
    if "goal" in s or re.fullmatch(r"g\d?", (group_id or "").lower()):
        return "goalies"
    if "injur" in s or (group_id or "").lower() in ("ir", "oi", "inj"):
        return "injuries"
    if "defen" in s or re.fullmatch(r"d\d", (group_id or "").lower()):
        return f"d{n or 1}"
    if "forward" in s or "line" in s or re.fullmatch(r"f\d", (group_id or "").lower()):
        return f"f{n or 1}"
    return (group_id or group_name or "other").lower()


def parse_lines(html_text: str) -> dict:
    data = next_data(html_text)
    if data is not None:
        path, rows = find_player_rows(data)
        if rows:
            groups: dict[str, list[str]] = {}
            injuries = []
            for r in rows:
                nm = str(_get(r, NAME_KEYS)).strip()
                rl = role(_get(r, GROUP_ID_KEYS), _get(r, GROUP_NAME_KEYS))
                if rl == "injuries":
                    injuries.append({"name": nm, "status": _get(r, INJURY_KEYS)})
                    continue
                groups.setdefault(rl, [])
                if nm not in groups[rl]:
                    groups[rl].append(nm)
            return {"source": "json", "path": path, "updated_at": find_updated(data), "groups": groups, "injuries": injuries}
    return {"source": "text", "text": strip_html(html_text)[:20000]}


def roles_by_player(lines: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for g, players in lines.get("groups", {}).items():
        for i, p in enumerate(players):
            r = out.setdefault(names.person(p), {"name": p, "line": None, "pp": None, "goalie": None})
            if re.fullmatch(r"[fd]\d", g):
                r["line"] = g
            elif re.fullmatch(r"pp\d", g):
                r["pp"] = g
            elif g == "goalies":
                r["goalie"] = "starter" if i == 0 else "backup"
    for inj in lines.get("injuries", []):
        r = out.setdefault(names.person(inj["name"]), {"name": inj["name"], "line": None, "pp": None, "goalie": None})
        r["injury"] = inj.get("status") or "injured"
    return out


def diff(old: dict, new: dict) -> list[dict]:
    """Player-level changes between two parsed line sets."""
    a, b = roles_by_player(old), roles_by_player(new)
    changes = []
    for key in sorted(set(a) | set(b)):
        pa, pb = a.get(key, {}), b.get(key, {})
        name = pb.get("name") or pa.get("name")
        for fld, label in (("line", "line"), ("pp", "pp_unit"), ("goalie", "goalie"), ("injury", "injury")):
            if pa.get(fld) != pb.get(fld):
                changes.append({"player": name, "field": label, "old": pa.get(fld), "new": pb.get(fld)})
    return changes


def sync_team(store: Store, team: str, client: httpx.Client | None = None) -> dict:
    team = names.team(team)
    slug = TEAM_SLUGS[team]
    http = client or httpx.Client(headers=UA, timeout=30, follow_redirects=True)
    r = http.get(URL.format(slug=slug))
    r.raise_for_status()
    parsed = parse_lines(r.text)
    if parsed["source"] != "json":
        return {"team": team, "parsed": False}
    with store.conn() as c:
        prev = c.execute("select lines from team_lines where team = %s order by fetched_at desc limit 1", (team,)).fetchone()
        same = prev is not None and prev["lines"].get("groups") == parsed["groups"] and prev["lines"].get("injuries") == parsed["injuries"]
        changes = []
        if not same:
            c.execute(
                "insert into team_lines (team, updated_at, lines) values (%s, %s, %s)",
                (team, parsed.get("updated_at"), json.dumps(parsed)),
            )
            if prev is not None:
                changes = diff(prev["lines"], parsed)
                for ch in changes:
                    c.execute(
                        "insert into lineup_changes (team, player, field, old_value, new_value) values (%s, %s, %s, %s, %s)",
                        (team, ch["player"], ch["field"], ch["old"], ch["new"]),
                    )
    return {"team": team, "parsed": True, "changed": not same, "changes": len(changes)}


def sync_all(store: Store, teams: list[str] | None = None) -> dict:
    out = {"teams": 0, "changed": 0, "changes": 0, "unparsed": []}
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as http:
        for t in teams or list(TEAM_SLUGS):
            try:
                res = sync_team(store, t, http)
            except Exception as e:  # noqa: BLE001
                log.warning("lines for %s failed: %s", t, e)
                out["unparsed"].append(t)
                continue
            out["teams"] += 1
            if not res["parsed"]:
                out["unparsed"].append(t)
            out["changed"] += int(res.get("changed", False))
            out["changes"] += res.get("changes", 0)
    return out


def latest(store: Store, team: str) -> dict | None:
    with store.conn() as c:
        row = c.execute(
            "select fetched_at, updated_at, lines from team_lines where team = %s order by fetched_at desc limit 1",
            (names.team(team),),
        ).fetchone()
    if not row:
        return None
    return {"team": names.team(team), "fetched_at": row["fetched_at"].isoformat(), "updated_at": row["updated_at"],
            "groups": row["lines"].get("groups"), "injuries": row["lines"].get("injuries")}


def recent_changes(store: Store, days: int = 3, teams: list[str] | None = None) -> list[dict]:
    q = "select team, player, field, old_value, new_value, detected_at from lineup_changes where detected_at >= now() - make_interval(days => %s)"
    args: list = [days]
    if teams:
        q += " and team = any(%s)"
        args.append([names.team(t) for t in teams])
    q += " order by detected_at desc limit 200"
    with store.conn() as c:
        rows = c.execute(q, args).fetchall()
    return [{"team": r["team"], "player": r["player"], "field": r["field"], "from": r["old_value"],
             "to": r["new_value"], "at": r["detected_at"].isoformat()} for r in rows]


def verify(team: str = "TOR") -> dict:
    """What the parser finds on one live team page."""
    r = httpx.get(URL.format(slug=TEAM_SLUGS[team]), headers=UA, timeout=30, follow_redirects=True)
    data = next_data(r.text)
    out: dict = {"status": r.status_code, "has_next_data": data is not None}
    if data is not None:
        path, rows = find_player_rows(data)
        out["rows_path"] = path
        out["row_keys"] = sorted(rows[0].keys()) if rows else []
        out["sample_rows"] = rows[:3]
    out["parsed"] = parse_lines(r.text) if data is not None else {"source": "text"}
    return out
