"""Postgres-backed DataSource plus the writes the ingest jobs and tools need."""

from __future__ import annotations

import json
from datetime import date, timedelta
from importlib import resources
from typing import Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import keeper, names
from .categories import SKATER_STATS
from .data import GoalieLine, IceTime, PlayerRef, StatLine

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
        self._keeper_cache: tuple[int | None, list[dict]] = (None, [])
        self._live_cache: dict[date, tuple[float, list[dict]]] = {}

    def conn(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)

    def init(self) -> None:
        sql = resources.files("puckdesk").joinpath("schema.sql").read_text()
        with self.conn() as c:
            c.execute(sql)

    # --- writes ---------------------------------------------------------------
    def upsert_players(self, rows: Iterable[dict], keep_team: bool = False) -> int:
        """keep_team: an existing player's team stays (for rows from past seasons)."""
        team_sql = "coalesce(players.team, excluded.team)" if keep_team else "coalesce(excluded.team, players.team)"
        sql = f"""insert into players (id, full_name, norm_name, initial_key, team, position, birth_date, updated_at)
                  values (%s, %s, %s, %s, %s, %s, %s, now())
                  on conflict (id) do update set full_name = excluded.full_name,
                    norm_name = excluded.norm_name, initial_key = excluded.initial_key, team = {team_sql},
                    position = coalesce(excluded.position, players.position),
                    birth_date = coalesce(excluded.birth_date, players.birth_date), updated_at = now()"""
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for r in rows:
                cur.execute(
                    sql,
                    (
                        r["id"], r["name"], names.person(r["name"]), names.initial_key(r["name"]),
                        names.team(r.get("team")) or None, r.get("position"), r.get("birth_date"),
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

    def save_digest(self, league: str, payload: dict, state: dict | None = None) -> int:
        with self.conn() as c:
            row = c.execute(
                "insert into digests (league, payload, state) values (%s, %s, %s) returning id",
                (league, Jsonb(payload), Jsonb(state) if state is not None else None),
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
                    f"select id, full_name, team, position, birth_date from players where {column} = %s and {pos_clause}",
                    (value,),
                ).fetchall()
                if len(rows) > 1:
                    rows = [r for r in rows if r["team"] == t] or rows
                if len(rows) == 1:
                    r = rows[0]
                    return PlayerRef(r["id"], r["full_name"], r["team"] or t, r["position"] or "", r["birth_date"])
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

    def players_missing_birth_date(self, limit: int) -> list[int]:
        """Players with stats this season or last but no birth date, skaters with the most games first."""
        with self.conn() as c:
            rows = c.execute(
                """select p.id from players p
                   left join (select player_id, count(*) n from skater_games group by player_id) g on g.player_id = p.id
                   left join (select player_id, max(gp) gp from season_totals group by player_id) t on t.player_id = p.id
                   where p.birth_date is null and (g.n is not null or t.gp is not null)
                   order by coalesce(g.n, 0) desc, coalesce(t.gp, 0) desc limit %s""",
                (limit,),
            ).fetchall()
        return [r["id"] for r in rows]

    def goalie_game_log(self, player_id: int, start: date, end: date) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """select g.game_id, coalesce(s.start_utc, (g.game_date + time '19:00') at time zone 'America/New_York') as start
                   from goalie_games g left join games s on s.id = g.game_id
                   where g.player_id = %s and g.game_date between %s and %s and (g.started or coalesce(g.toi_sec, 0) > 0)""",
                (player_id, start, end),
            ).fetchall()
        return [{"game_id": r["game_id"], "start": r["start"]} for r in rows]

    def live_games(self, dates: list[date], max_age: float = 90.0) -> list[dict]:
        """Started games from the NHL score feed, cached briefly; box scores only for games not ingested yet."""
        import time as _time

        from . import nhl

        out: list[dict] = []
        for d in dates:
            cached = self._live_cache.get(d)
            if cached and _time.monotonic() - cached[0] < max_age:
                out += cached[1]
                continue
            with self.conn() as c:
                ingested = {r["game_id"] for r in c.execute(
                    "select distinct game_id from goalie_games where game_date = %s", (d,)).fetchall()}
            games = nhl.live_games(nhl.NHL(), d, ingested)
            self._live_cache[d] = (_time.monotonic(), games)
            out += games
        return out

    def set_birth_date(self, player_id: int, birth: str | date) -> None:
        with self.conn() as c:
            c.execute("update players set birth_date = %s where id = %s", (birth, player_id))

    # --- value signals -----------------------------------------------------------
    def team_roles(self, team: str) -> dict[str, dict]:
        with self.conn() as c:
            row = c.execute(
                "select lines from team_lines where team = %s order by fetched_at desc limit 1", (names.team(team),)
            ).fetchone()
        roles: dict[str, dict] = {}
        for group, players in ((row["lines"].get("groups") or {}) if row else {}).items():
            g = group.lower()
            if not (g[:1] in ("f", "d") and g[1:].isdigit()) and g not in ("pp1", "pp2"):
                continue
            for name in players:
                r = roles.setdefault(names.person(name), {"line": None, "pp": None})
                if g.startswith("pp"):
                    r["pp"] = r["pp"] or g
                else:
                    r["line"] = r["line"] or g
        return roles

    def ice_time(self, player_id: int, as_of: date) -> IceTime:
        season = season_of(as_of)
        with self.conn() as c:
            now = c.execute(
                """select count(*) gp, avg(toi_sec) / 60.0 toi, avg(pp_toi_sec) / 60.0 pp_toi from skater_games
                   where player_id = %s and season = %s and game_date < %s""",
                (player_id, season, as_of),
            ).fetchone()
            prior = c.execute(
                "select stats from season_totals where player_id = %s and season = %s and kind = 'skater'",
                (player_id, previous_season(season)),
            ).fetchone()
        st = prior["stats"] if prior else {}
        toi_p, pp_p = st.get("toi_per_game_sec"), st.get("pp_toi_per_game_sec")
        return IceTime(
            gp=int(now["gp"]),
            toi=float(now["toi"]) if now["toi"] is not None else None,
            pp_toi=float(now["pp_toi"]) if now["pp_toi"] is not None else None,
            toi_prior=toi_p / 60.0 if toi_p else None,
            pp_toi_prior=pp_p / 60.0 if pp_p is not None else None,
        )

    def save_rostered(self, day: date, rows: list[dict]) -> int:
        """Daily % rostered snapshot: rows of {name, team, pct}; the latest value of the day wins."""
        n = 0
        with self.conn() as c, c.cursor() as cur:
            for r in rows:
                if r.get("pct") is None:
                    continue
                cur.execute(
                    """insert into rostered_snapshots (snap_date, norm_name, name, team, pct) values (%s, %s, %s, %s, %s)
                       on conflict (snap_date, norm_name) do update set pct = excluded.pct, team = excluded.team""",
                    (day, names.person(r["name"]), r["name"], names.team(r.get("team")) or None, float(r["pct"])),
                )
                n += 1
        return n

    def rostered_trend(self, norm_name: str, as_of: date) -> tuple[float, float, int] | None:
        with self.conn() as c:
            rows = c.execute(
                """select snap_date, pct from rostered_snapshots where norm_name = %s
                   and snap_date between %s and %s order by snap_date""",
                (norm_name, as_of - timedelta(days=14), as_of),
            ).fetchall()
        if len(rows) < 2:
            return None
        last = rows[-1]
        # Compare with the snapshot nearest a week before the latest one (at least 2 days back).
        older = [r for r in rows if (last["snap_date"] - r["snap_date"]).days >= 2]
        if not older:
            return None
        base = min(older, key=lambda r: abs((last["snap_date"] - r["snap_date"]).days - 7))
        return float(last["pct"]), float(base["pct"]), (last["snap_date"] - base["snap_date"]).days

    def keeper_ranks(self) -> list[dict]:
        with self.conn() as c:
            row = c.execute(
                "select id, content from articles where title ilike %s order by published desc nulls last limit 1",
                (keeper.TITLE_PATTERN,),
            ).fetchone()
        if not row:
            return []
        if self._keeper_cache[0] != row["id"]:
            self._keeper_cache = (row["id"], keeper.parse(row["content"] or ""))
        return self._keeper_cache[1]

    def lineup_posts(self, days: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """select account, text, posted_at from posts where kind = 'lines'
                   and posted_at >= now() - make_interval(days => %s) order by posted_at desc""",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    def last_name_teams(self) -> dict[str, list[str]]:
        with self.conn() as c:
            rows = c.execute("select full_name, team from players where team is not null").fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(names.last(r["full_name"]), []).append(r["team"])
        return out

    def media_texts(self, query: str, days: int, limit: int = 40) -> list[dict]:
        """Full texts of podcast windows, articles and posts that match a search, newest first."""
        sql = """
        with q as (select websearch_to_tsquery('english', %(q)s) as query)
        select * from (
          select 'podcast' as kind, e.podcast as source, e.title, e.published as at, w.start_sec, e.id as ref,
                 e.link, w.text
            from transcript_windows w join podcast_episodes e on e.id = w.episode_id, q
           where w.tsv @@ q.query and e.published >= now() - make_interval(days => %(days)s)
          union all
          select 'article', a.source, a.title, a.published, null, a.id, a.link, a.content
            from articles a, q
           where a.tsv @@ q.query and a.published >= now() - make_interval(days => %(days)s)
          union all
          select 'post', p.account, left(p.text, 80), p.posted_at, null, null, p.url, p.text
            from posts p, q
           where p.tsv @@ q.query and p.posted_at >= now() - make_interval(days => %(days)s)
        ) hits order by at desc nulls last limit %(limit)s
        """
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, {"q": query, "days": days, "limit": limit}).fetchall()]

    # --- strategy and news notes ---------------------------------------------------
    def strategy(self, league: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("select strategy, note, updated_at from league_strategy where league = %s", (league,)).fetchone()
        return {"strategy": row["strategy"], "note": row["note"], "updated_at": row["updated_at"].isoformat()} if row else None

    def set_strategy(self, league: str, strategy: str, note: str | None) -> None:
        with self.conn() as c:
            c.execute(
                """insert into league_strategy (league, strategy, note) values (%s, %s, %s)
                   on conflict (league) do update set strategy = excluded.strategy, note = excluded.note, updated_at = now()""",
                (league, strategy, note),
            )

    def set_news_notes(self, league: str, notes: dict[str, str]) -> int | None:
        """Merge one-line takeaways into the latest digest's news_notes; returns the digest id."""
        with self.conn() as c:
            row = c.execute(
                """update digests set payload = jsonb_set(payload, '{news_notes}',
                       coalesce(payload->'news_notes', '{}'::jsonb) || %s::jsonb)
                   where id = (select id from digests where league = %s order by created_at desc limit 1)
                   returning id""",
                (Jsonb(notes), league),
            ).fetchone()
        return row["id"] if row else None

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
