"""NHL data: schedule and rosters from api-web.nhle.com, per-game and season
stats from the stats REST API (api.nhle.com/stats/rest).

Both APIs are public but undocumented. Field names are read defensively, and
`puckdesk verify-nhl` prints what the endpoints actually return so the mapping
can be checked against the live API.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterator

import httpx

from . import names
from .store import Store, season_of

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
PAGE = 100
log = logging.getLogger("puckdesk.nhl")


class NHL:
    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=30, headers={"User-Agent": "puckdesk/0.1"}, follow_redirects=True)

    def get(self, url: str, params: dict | None = None) -> Any:
        for attempt in range(4):
            try:
                r = self.http.get(url, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError("retry", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == 3:
                    raise
                wait = 2 ** attempt
                log.warning("GET %s failed (%s), retrying in %ss", url, e, wait)
                time.sleep(wait)

    # --- schedule & rosters ------------------------------------------------------
    def schedule_week(self, day: date) -> dict:
        return self.get(f"{WEB}/schedule/{day.isoformat()}")

    def roster(self, team: str) -> dict:
        return self.get(f"{WEB}/roster/{team}/current")

    # --- stats REST ------------------------------------------------------------
    def report(self, entity: str, report: str, cayenne: str, per_game: bool) -> Iterator[dict]:
        """Page through a stats report. entity: skater or goalie."""
        sort = [{"property": "playerId", "direction": "ASC"}]
        if per_game:
            sort.append({"property": "gameId", "direction": "ASC"})
        start = 0
        while True:
            params = {
                "isAggregate": "false",
                "isGame": "true" if per_game else "false",
                "start": start,
                "limit": PAGE,
                "sort": json.dumps(sort),
                "cayenneExp": cayenne,
            }
            body = self.get(f"{STATS}/{entity}/{report}", params)
            rows = body.get("data", [])
            yield from rows
            start += len(rows)
            if not rows or start >= body.get("total", 0):
                break


# --- parsing (pure functions, tested with sample rows) ------------------------------
def _pick(row: dict, *keys, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _team(row: dict) -> str | None:
    t = _pick(row, "teamAbbrev", "teamAbbrevs")
    if not t:
        return None
    return names.team(str(t).split(",")[-1])  # traded players list teams oldest first


def _int(v) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def parse_schedule(body: dict) -> list[dict]:
    games = []
    for day in body.get("gameWeek", []):
        for g in day.get("games", []):
            games.append(
                {
                    "id": g["id"],
                    "season": _int(g.get("season")),
                    "game_type": _int(g.get("gameType")),
                    "game_date": _date(day["date"]),
                    "start_utc": g.get("startTimeUTC"),
                    "home": names.team(g["homeTeam"]["abbrev"]),
                    "away": names.team(g["awayTeam"]["abbrev"]),
                    "state": g.get("gameState"),
                }
            )
    return games


def parse_roster(team: str, body: dict) -> list[dict]:
    out = []
    for group in ("forwards", "defensemen", "goalies"):
        for p in body.get(group, []):
            first = p.get("firstName", {}).get("default", "")
            last = p.get("lastName", {}).get("default", "")
            out.append({"id": p["id"], "name": f"{first} {last}".strip(), "team": team, "position": p.get("positionCode")})
    return out


SKATER_FIELDS = {
    # our stat: candidate field names in the stats REST reports
    "goals": ("goals",),
    "assists": ("assists",),
    "points": ("points",),
    "pp_points": ("ppPoints", "powerPlayPoints"),
    "sh_points": ("shPoints", "shorthandedPoints"),
    "gwg": ("gameWinningGoals",),
    "shots": ("shots", "shotsOnGoal"),
    "pim": ("penaltyMinutes", "pim"),
    "plus_minus": ("plusMinus",),
    "hits": ("hits",),
    "blocks": ("blockedShots", "blocks"),
    "faceoff_wins": ("totalFaceoffWins", "faceoffWins", "faceoffsWon"),
}


def merge_skater_rows(reports: dict[str, list[dict]], per_game: bool) -> dict[tuple, dict]:
    """Join summary/realtime/faceoffwins/powerplay rows on player (and game)."""
    merged: dict[tuple, dict] = {}
    for rows in reports.values():
        for r in rows:
            key = (r.get("playerId"), r.get("gameId") if per_game else None)
            merged.setdefault(key, {}).update({k: v for k, v in r.items() if v is not None})
    return merged


def skater_game_rows(merged: dict[tuple, dict]) -> list[dict]:
    out = []
    for (pid, gid), r in merged.items():
        if pid is None or gid is None:
            continue
        gd = _date(_pick(r, "gameDate"))
        row = {
            "player_id": pid,
            "game_id": gid,
            "game_date": gd,
            "season": season_of(gd),
            "team": _team(r),
            "toi_sec": _int(_pick(r, "timeOnIcePerGame", "timeOnIce")),
            "pp_toi_sec": _int(_pick(r, "ppTimeOnIce", "ppTimeOnIcePerGame")),
        }
        for stat, fields in SKATER_FIELDS.items():
            row[stat] = _int(_pick(r, *fields, default=0))
        out.append(row)
    return out


def skater_season_rows(merged: dict[tuple, dict], season: int) -> list[dict]:
    out = []
    for (pid, _), r in merged.items():
        if pid is None:
            continue
        gp = _int(_pick(r, "gamesPlayed", default=0))
        if gp <= 0:
            continue
        stats = {stat: _int(_pick(r, *fields, default=0)) for stat, fields in SKATER_FIELDS.items()}
        out.append({"player_id": pid, "season": season, "kind": "skater", "gp": gp, "stats": stats})
    return out


def goalie_game_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        pid, gid = r.get("playerId"), r.get("gameId")
        if pid is None or gid is None:
            continue
        gd = _date(_pick(r, "gameDate"))
        ga = _int(_pick(r, "goalsAgainst", default=0))
        out.append(
            {
                "player_id": pid,
                "game_id": gid,
                "game_date": gd,
                "season": season_of(gd),
                "team": _team(r),
                "started": _int(_pick(r, "gamesStarted", default=0)) > 0,
                "wins": _int(_pick(r, "wins", default=0)),
                "saves": _int(_pick(r, "saves", default=0)),
                "shots_against": _int(_pick(r, "shotsAgainst", default=0)),
                "goals_against": ga,
                "shutouts": _int(_pick(r, "shutouts", default=0)),
                "toi_sec": _int(_pick(r, "timeOnIce", default=0)),
            }
        )
    return out


def goalie_season_rows(rows: list[dict], season: int) -> list[dict]:
    out = []
    for r in rows:
        pid = r.get("playerId")
        gp = _int(_pick(r, "gamesPlayed", default=0))
        if pid is None or gp <= 0:
            continue
        stats = {
            "starts": _int(_pick(r, "gamesStarted", default=gp)),
            "wins": _int(_pick(r, "wins", default=0)),
            "saves": _int(_pick(r, "saves", default=0)),
            "shots_against": _int(_pick(r, "shotsAgainst", default=0)),
            "goals_against": _int(_pick(r, "goalsAgainst", default=0)),
            "shutouts": _int(_pick(r, "shutouts", default=0)),
            "team_games": 82,
        }
        out.append({"player_id": pid, "season": season, "kind": "goalie", "gp": gp, "stats": stats})
    return out


def player_rows_from_stats(rows: list[dict], goalie: bool) -> list[dict]:
    """Names and positions from stats rows, for players not on a current roster."""
    seen: dict[int, dict] = {}
    for r in rows:
        pid = r.get("playerId")
        name = _pick(r, "goalieFullName" if goalie else "skaterFullName", "fullName")
        if pid is None or not name:
            continue
        seen[pid] = {"id": pid, "name": name, "team": _team(r), "position": "G" if goalie else _pick(r, "positionCode")}
    return list(seen.values())


# --- ingest jobs -------------------------------------------------------------------
SKATER_REPORTS = ("summary", "realtime", "faceoffwins", "powerplay")


def sync_schedule(nhl: NHL, store: Store, season: int) -> int:
    y = season // 10000
    day, end = date(y, 9, 1), date(y + 1, 6, 30)
    total = 0
    while day <= end:
        body = nhl.schedule_week(day)
        total += store.upsert_games(parse_schedule(body))
        nxt = body.get("nextStartDate")
        day = _date(nxt) if nxt else day + timedelta(days=7)
    return total


def sync_rosters(nhl: NHL, store: Store) -> int:
    with store.conn() as c:
        teams = [r["team"] for r in c.execute(
            "select distinct home team from games where game_type = 2 union select distinct away from games where game_type = 2"
        ).fetchall()]
    total = 0
    for t in sorted(teams):
        try:
            total += store.upsert_players(parse_roster(t, nhl.roster(t)))
        except httpx.HTTPError as e:
            log.warning("roster %s failed: %s", t, e)
    return total


def sync_games(nhl: NHL, store: Store, start: date, end: date) -> dict:
    cay = f'gameDate>="{start.isoformat()}" and gameDate<="{end.isoformat()}" and gameTypeId=2'
    reports = {rep: list(nhl.report("skater", rep, cay, per_game=True)) for rep in SKATER_REPORTS}
    merged = merge_skater_rows(reports, per_game=True)
    store.upsert_players(player_rows_from_stats(reports["summary"], goalie=False))
    n_sk = store.upsert_skater_games(skater_game_rows(merged))
    g_rows = list(nhl.report("goalie", "summary", cay, per_game=True))
    store.upsert_players(player_rows_from_stats(g_rows, goalie=True))
    n_g = store.upsert_goalie_games(goalie_game_rows(g_rows))
    return {"skater_games": n_sk, "goalie_games": n_g}


def sync_priors(nhl: NHL, store: Store, season: int) -> dict:
    cay = f"seasonId={season} and gameTypeId=2"
    reports = {rep: list(nhl.report("skater", rep, cay, per_game=False)) for rep in SKATER_REPORTS}
    merged = merge_skater_rows(reports, per_game=False)
    store.upsert_players(player_rows_from_stats(reports["summary"], goalie=False))
    n_sk = store.upsert_season_totals(skater_season_rows(merged, season))
    g_rows = list(nhl.report("goalie", "summary", cay, per_game=False))
    store.upsert_players(player_rows_from_stats(g_rows, goalie=True))
    n_g = store.upsert_season_totals(goalie_season_rows(g_rows, season))
    return {"skaters": n_sk, "goalies": n_g}


def verify(nhl: NHL) -> dict:
    """Fetch one small sample from every endpoint we use and report the fields."""
    out: dict[str, Any] = {}
    today = datetime.now().date()
    try:
        body = nhl.schedule_week(today)
        games = parse_schedule(body)
        out["schedule"] = {"games_this_week": len(games), "first": games[0] if games else None}
    except Exception as e:  # noqa: BLE001
        out["schedule"] = f"error: {e}"
    try:
        body = nhl.roster("TOR")
        out["roster_TOR"] = {"players": len(parse_roster("TOR", body)), "sample": parse_roster("TOR", body)[:1]}
    except Exception as e:  # noqa: BLE001
        out["roster_TOR"] = f"error: {e}"
    season = season_of(today) - 10001  # last season
    cay = f"seasonId={season} and gameTypeId=2"
    for entity, rep in [("skater", r) for r in SKATER_REPORTS] + [("goalie", "summary")]:
        try:
            body = nhl.get(
                f"{STATS}/{entity}/{rep}",
                {"isAggregate": "false", "isGame": "true", "start": 0, "limit": 2, "cayenneExp": cay,
                 "sort": json.dumps([{"property": "playerId", "direction": "ASC"}])},
            )
            rows = body.get("data", [])
            out[f"{entity}/{rep}"] = {"total": body.get("total"), "fields": sorted(rows[0].keys()) if rows else []}
        except Exception as e:  # noqa: BLE001
            out[f"{entity}/{rep}"] = f"error: {e}"
    return out
