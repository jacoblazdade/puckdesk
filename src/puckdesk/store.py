"""Postgres-backed DataSource plus the writes the ingest jobs and tools need."""

from __future__ import annotations

import json
from datetime import date, timedelta
from importlib import resources
from typing import Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import names
from .categories import SKATER_STATS
from .data import GoalieLine, PlayerRef, StatLine

RECENT_DAYS = 14


def season_of(d: date) -> int:
    """20262027 for any date from September 2026 to August 2027."""
    y = d.year if d.month >= 9 else d.year - 1
    return y * 10000 + (y + 1)


def previous_season(season: int) -> int:
    y = season // 10000 - 1
    return y * 10000 + (y + 1)


class Store:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def conn(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)

    def init(self) -> None:
        sql = resources.files("puckdesk").joinpath("schema.sql").read_text()
        with self.conn() as c:
            c.execute(sql)

    # --- writes ---------------------------------------------------------------
    def upsert_players(self, rows: Iterable[dict]) -> int:
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for r in rows:
                cur.execute(
                    """insert into players (id, full_name, norm_name, initial_key, team, position, updated_at)
                       values (%s, %s, %s, %s, %s, %s, now())
                       on conflict (id) do update set full_name = excluded.full_name,
                         norm_name = excluded.norm_name, initial_key = excluded.initial_key,
                         team = coalesce(excluded.team, players.team),
                         position = coalesce(excluded.position, players.position), updated_at = now()""",
                    (
                        r["id"], r["name"], names.person(r["name"]), names.initial_key(r["name"]),
                        names.team(r.get("team")) or None, r.get("position"),
                    ),
                )
                n += 1
        return n

    def upsert_games(self, rows: Iterable[dict]) -> int:
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for g in rows:
                cur.execute(
                    """insert into games (id, season, game_type, game_date, start_utc, home, away, state)
                       values (%(id)s, %(season)s, %(game_type)s, %(game_date)s, %(start_utc)s, %(home)s, %(away)s, %(state)s)
                       on conflict (id) do update set game_date = excluded.game_date, start_utc = excluded.start_utc,
                         state = excluded.state, home = excluded.home, away = excluded.away""",
                    g,
                )
                n += 1
        return n

    def upsert_skater_games(self, rows: Iterable[dict]) -> int:
        cols = ["player_id", "game_id", "game_date", "season", "team", *SKATER_STATS, "toi_sec", "pp_toi_sec"]
        sql = (
            f"insert into skater_games ({', '.join(cols)}) values ({', '.join('%(' + c + ')s' for c in cols)}) "
            f"on conflict (player_id, game_id) do update set "
            + ", ".join(f"{c} = excluded.{c}" for c in cols[2:])
        )
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for r in rows:
                cur.execute(sql, {k: r.get(k) for k in cols})
                n += 1
        return n

    def upsert_goalie_games(self, rows: Iterable[dict]) -> int:
        cols = ["player_id", "game_id", "game_date", "season", "team", "started", "wins", "saves",
                "shots_against", "goals_against", "shutouts", "toi_sec"]
        sql = (
            f"insert into goalie_games ({', '.join(cols)}) values ({', '.join('%(' + c + ')s' for c in cols)}) "
            f"on conflict (player_id, game_id) do update set "
            + ", ".join(f"{c} = excluded.{c}" for c in cols[2:])
        )
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for r in rows:
                cur.execute(sql, {k: r.get(k) for k in cols})
                n += 1
        return n

    def upsert_season_totals(self, rows: Iterable[dict]) -> int:
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for r in rows:
                cur.execute(
                    """insert into season_totals (player_id, season, kind, gp, stats) values (%s, %s, %s, %s, %s)
                       on conflict (player_id, season, kind) do update set gp = excluded.gp, stats = excluded.stats""",
                    (r["player_id"], r["season"], r["kind"], r["gp"], Jsonb(r["stats"])),
                )
                n += 1
        return n

    def set_tags(self, league: str, tags: list[dict]) -> int:
        with self.conn() as c, c.cursor() as cur:
            for t in tags:
                cur.execute(
                    """insert into tags (league, norm_name, name, tag) values (%s, %s, %s, %s)
                       on conflict (league, norm_name) do update set tag = excluded.tag, name = excluded.name, updated_at = now()""",
                    (league, names.person(t["name"]), t["name"], t["tag"]),
                )
        return len(tags)

    def save_digest(self, league: str, payload: dict) -> int:
        with self.conn() as c:
            row = c.execute(
                "insert into digests (league, payload) values (%s, %s) returning id", (league, Jsonb(payload))
            ).fetchone()
        return row["id"]

    def latest_digest(self, league: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "select id, created_at, payload from digests where league = %s order by created_at desc limit 1", (league,)
            ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "created_at": row["created_at"].isoformat(), **row["payload"]}

    def league_summaries(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """select l.league, max(d.created_at) last_digest from
                   (select distinct league from digests union select distinct league from tags) l
                   left join digests d on d.league = l.league group by l.league
                   order by l.league"""
            ).fetchall()
        return [{"name": r["league"], "last_digest": r["last_digest"].isoformat() if r["last_digest"] else None} for r in rows]

    def leagues(self) -> list[str]:
        with self.conn() as c:
            rows = c.execute("select distinct league from digests union select distinct league from tags").fetchall()
        return sorted(r["league"] for r in rows)

    # --- DataSource -------------------------------------------------------------
    def find_player(self, name: str, team: str, goalie: bool) -> PlayerRef | None:
        t = names.team(team)
        pos_clause = "position = 'G'" if goalie else "coalesce(position, '') <> 'G'"
        with self.conn() as c:
            for column, value in (("norm_name", names.person(name)), ("initial_key", names.initial_key(name))):
                rows = c.execute(
                    f"select id, full_name, team, position from players where {column} = %s and {pos_clause}", (value,)
                ).fetchall()
                if len(rows) > 1:
                    rows = [r for r in rows if r["team"] == t] or rows
                if len(rows) == 1:
                    r = rows[0]
                    return PlayerRef(r["id"], r["full_name"], r["team"] or t, r["position"] or "")
        return None

    def team_dates(self, team: str, start: date, end: date) -> list[date]:
        t = names.team(team)
        with self.conn() as c:
            rows = c.execute(
                """select game_date from games where game_type = 2 and (home = %s or away = %s)
                   and game_date between %s and %s order by game_date""",
                (t, t, start, end),
            ).fetchall()
        return [r["game_date"] for r in rows]

    def games_per_date(self, start: date, end: date) -> dict[date, int]:
        with self.conn() as c:
            rows = c.execute(
                "select game_date, count(*) n from games where game_type = 2 and game_date between %s and %s group by game_date",
                (start, end),
            ).fetchall()
        counts = {r["game_date"]: r["n"] for r in rows}
        out, d = {}, start
        while d <= end:
            out[d] = counts.get(d, 0)
            d += timedelta(days=1)
        return out

    def all_team_dates(self, start: date, end: date) -> dict[str, list[date]]:
        with self.conn() as c:
            rows = c.execute(
                """select team, game_date from (
                       select home team, game_date from games where game_type = 2 and game_date between %s and %s
                       union all
                       select away team, game_date from games where game_type = 2 and game_date between %s and %s
                   ) t order by team, game_date""",
                (start, end, start, end),
            ).fetchall()
        out: dict[str, list[date]] = {}
        for r in rows:
            out.setdefault(r["team"], []).append(r["game_date"])
        return out

    def skater_lines(self, player_id: int, as_of: date) -> tuple[StatLine, StatLine, StatLine | None]:
        season = season_of(as_of)
        sums = ", ".join(f"coalesce(sum({s}), 0) as {s}" for s in SKATER_STATS)
        q = f"select count(*) gp, {sums} from skater_games where player_id = %s and season = %s and game_date < %s"
        with self.conn() as c:
            s_row = c.execute(q, (player_id, season, as_of)).fetchone()
            r_row = c.execute(q + " and game_date >= %s", (player_id, season, as_of, as_of - timedelta(days=RECENT_DAYS))).fetchone()
            p_row = c.execute(
                "select gp, stats from season_totals where player_id = %s and season = %s and kind = 'skater'",
                (player_id, previous_season(season)),
            ).fetchone()

        def line(row) -> StatLine:
            return StatLine(int(row["gp"]), {s: float(row[s]) for s in SKATER_STATS})

        prior = StatLine(int(p_row["gp"]), {s: float(p_row["stats"].get(s, 0)) for s in SKATER_STATS}) if p_row else None
        return line(s_row), line(r_row), prior

    def goalie_lines(self, player_id: int, as_of: date) -> tuple[GoalieLine, GoalieLine | None]:
        season = season_of(as_of)
        with self.conn() as c:
            row = c.execute(
                """select count(*) gp, coalesce(sum(started::int), 0) starts, coalesce(sum(saves), 0) saves,
                          coalesce(sum(shots_against), 0) sa, coalesce(sum(goals_against), 0) ga, coalesce(sum(wins), 0) wins
                   from goalie_games where player_id = %s and season = %s and game_date < %s""",
                (player_id, season, as_of),
            ).fetchone()
            team_row = c.execute("select team from players where id = %s", (player_id,)).fetchone()
            team_games = 0
            if team_row and team_row["team"]:
                team_games = c.execute(
                    """select count(*) n from games where game_type = 2 and season = %s and game_date < %s
                       and (home = %s or away = %s)""",
                    (season, as_of, team_row["team"], team_row["team"]),
                ).fetchone()["n"]
            p_row = c.execute(
                "select gp, stats from season_totals where player_id = %s and season = %s and kind = 'goalie'",
                (player_id, previous_season(season)),
            ).fetchone()
        cur = GoalieLine(
            int(row["gp"]), int(row["starts"]), float(row["saves"]), float(row["sa"]), float(row["ga"]), float(row["wins"]), int(team_games)
        )
        prior = None
        if p_row:
            s = p_row["stats"]
            prior = GoalieLine(
                int(p_row["gp"]), int(s.get("starts", p_row["gp"])), float(s.get("saves", 0)), float(s.get("shots_against", 0)),
                float(s.get("goals_against", 0)), float(s.get("wins", 0)), int(s.get("team_games", 82)),
            )
        return cur, prior

    def tags(self, league: str) -> dict[str, str]:
        with self.conn() as c:
            rows = c.execute("select norm_name, tag from tags where league = %s", (league,)).fetchall()
        return {r["norm_name"]: r["tag"] for r in rows}

    def goalie_hints(self, start: date, end: date) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "select game_date, norm_name, team, status from goalie_guesses where game_date between %s and %s",
                (start, end),
            ).fetchall()
        return [dict(r) for r in rows]

    def goalie_appearances(self, player_id: int, start: date, end: date) -> int:
        with self.conn() as c:
            row = c.execute(
                """select count(*) n from goalie_games where player_id = %s and game_date between %s and %s
                   and (started or coalesce(toi_sec, 0) > 0)""",
                (player_id, start, end),
            ).fetchone()
        return int(row["n"])

    def tag_list(self, league: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("select name, tag, updated_at from tags where league = %s order by tag, name", (league,)).fetchall()
        return [{"name": r["name"], "tag": r["tag"], "updated_at": r["updated_at"].isoformat()} for r in rows]

    def status(self) -> dict:
        with self.conn() as c:
            q = lambda sql: c.execute(sql).fetchone()
            return {
                "players": q("select count(*) n from players")["n"],
                "games": q("select count(*) n from games")["n"],
                "skater_games": q("select count(*) n, max(game_date) last from skater_games"),
                "goalie_games": q("select count(*) n, max(game_date) last from goalie_games"),
                "season_totals": q("select count(*) n from season_totals")["n"],
            }


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
