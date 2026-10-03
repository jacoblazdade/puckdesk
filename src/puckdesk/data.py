"""What the engine needs from the data layer, and an in-memory version of it.

The Postgres store (store.py) implements the same interface from real NHL data.
The in-memory version keeps tests and experiments free of a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from . import names


@dataclass
class StatLine:
    """Summed stats over some games."""

    gp: int = 0
    sums: dict[str, float] = field(default_factory=dict)

    def rate(self, stat: str) -> float | None:
        if self.gp <= 0:
            return None
        return self.sums.get(stat, 0.0) / self.gp


@dataclass
class GoalieLine:
    gp: int = 0
    starts: int = 0
    saves: float = 0.0
    shots_against: float = 0.0
    goals_against: float = 0.0
    wins: float = 0.0
    team_games: int = 0  # team games played over the same span, for start share


@dataclass
class PlayerRef:
    id: int
    name: str
    team: str
    position: str  # C, L, R, D or G
    birth_date: date | None = None


@dataclass
class IceTime:
    """Ice time per game in minutes, this season and last."""

    gp: int = 0
    toi: float | None = None
    pp_toi: float | None = None
    toi_prior: float | None = None
    pp_toi_prior: float | None = None


class DataSource(Protocol):
    def find_player(self, name: str, team: str, goalie: bool) -> PlayerRef | None: ...

    def team_dates(self, team: str, start: date, end: date) -> list[date]: ...

    def games_per_date(self, start: date, end: date) -> dict[date, int]: ...

    def all_team_dates(self, start: date, end: date) -> dict[str, list[date]]: ...

    def skater_lines(self, player_id: int, as_of: date) -> tuple[StatLine, StatLine, StatLine | None]:
        """(season to date, last 14 days, last season) before `as_of`."""
        ...

    def goalie_lines(self, player_id: int, as_of: date) -> tuple[GoalieLine, GoalieLine | None]:
        """(season to date, last season) before `as_of`."""
        ...

    def tags(self, league: str) -> dict[str, str]:
        """Stored tags keyed by normalised player name."""
        ...

    def goalie_hints(self, start: date, end: date) -> list[dict]:
        """Starting-goalie guesses: [{game_date, norm_name, team, status}]."""
        ...

    def goalie_appearances(self, player_id: int, start: date, end: date) -> int:
        """NHL games the goalie got into (any ice time) from start to end, inclusive."""
        ...

    # Value signals (value.py). Optional: missing methods mean no signal.
    def team_roles(self, team: str) -> dict[str, dict]:
        """Daily Faceoff roles keyed by normalised name: {"line": "f1".."f4"/"d1".."d3", "pp": "pp1"/"pp2"}."""
        ...

    def ice_time(self, player_id: int, as_of: date) -> IceTime: ...

    def rostered_trend(self, norm_name: str, as_of: date) -> tuple[float, float, int] | None:
        """(% rostered now, % about a week earlier, days between) from the daily snapshots."""
        ...

    def keeper_ranks(self) -> list[dict]:
        """Dobber's latest Top 300 Keeper League table: [{rank, name, team, defense, rating, change}]."""
        ...

    def lineup_posts(self, days: int) -> list[dict]:
        """Recent Game Day Tweets lineup posts: [{account, text, posted_at}]."""
        ...

    def last_name_teams(self) -> dict[str, list[str]]:
        """Teams of the current NHL players with each normalised last name (one entry per player)."""
        ...

    def strategy(self, league: str) -> dict | None:
        """Stored strategy override for a league: {"strategy", "note"}."""
        ...


class MemoryData:
    """A small in-memory DataSource for tests and what-if experiments."""

    def __init__(self) -> None:
        self.players: dict[int, PlayerRef] = {}
        self.schedule: dict[str, set[date]] = {}
        self.season: dict[int, StatLine] = {}
        self.recent: dict[int, StatLine] = {}
        self.prior: dict[int, StatLine] = {}
        self.goalie_season: dict[int, GoalieLine] = {}
        self.goalie_prior: dict[int, GoalieLine] = {}
        self._tags: dict[str, dict[str, str]] = {}
        self.hints: list[dict] = []
        self.appearances: dict[int, list[date]] = {}
        self.roles: dict[str, dict[str, dict]] = {}
        self.ice: dict[int, IceTime] = {}
        self.rostered: dict[str, tuple[float, float, int]] = {}
        self.keeper: list[dict] = []
        self.posts: list[dict] = []
        self.strategies: dict[str, dict] = {}

    # --- building ---------------------------------------------------------
    def add_player(self, pid: int, name: str, team: str, position: str, birth_date: date | None = None) -> None:
        self.players[pid] = PlayerRef(pid, name, names.team(team), position, birth_date)

    def add_games(self, team: str, dates: list[date]) -> None:
        self.schedule.setdefault(names.team(team), set()).update(dates)

    def set_tag(self, league: str, name: str, tag: str) -> None:
        self._tags.setdefault(league, {})[names.person(name)] = tag

    # --- DataSource -------------------------------------------------------
    def find_player(self, name: str, team: str, goalie: bool) -> PlayerRef | None:
        n, k, t = names.person(name), names.initial_key(name), names.team(team)
        candidates = [p for p in self.players.values() if (p.position == "G") == goalie]
        for match in (
            lambda p: names.person(p.name) == n and p.team == t,
            lambda p: names.person(p.name) == n,
            lambda p: names.initial_key(p.name) == k and p.team == t,
        ):
            hits = [p for p in candidates if match(p)]
            if len(hits) == 1:
                return hits[0]
        return None

    def team_dates(self, team: str, start: date, end: date) -> list[date]:
        return sorted(d for d in self.schedule.get(names.team(team), set()) if start <= d <= end)

    def games_per_date(self, start: date, end: date) -> dict[date, int]:
        counts: dict[date, int] = {}
        d = start
        while d <= end:
            n = sum(1 for dates in self.schedule.values() if d in dates)
            counts[d] = n // 2 if n else 0  # two teams per game
            d += timedelta(days=1)
        return counts

    def all_team_dates(self, start: date, end: date) -> dict[str, list[date]]:
        return {t: self.team_dates(t, start, end) for t in sorted(self.schedule)}

    def skater_lines(self, player_id: int, as_of: date) -> tuple[StatLine, StatLine, StatLine | None]:
        return (
            self.season.get(player_id, StatLine()),
            self.recent.get(player_id, StatLine()),
            self.prior.get(player_id),
        )

    def goalie_lines(self, player_id: int, as_of: date) -> tuple[GoalieLine, GoalieLine | None]:
        return self.goalie_season.get(player_id, GoalieLine()), self.goalie_prior.get(player_id)

    def tags(self, league: str) -> dict[str, str]:
        return dict(self._tags.get(league, {}))

    def goalie_hints(self, start: date, end: date) -> list[dict]:
        return [h for h in self.hints if start <= h["game_date"] <= end]

    def goalie_appearances(self, player_id: int, start: date, end: date) -> int:
        return sum(1 for d in self.appearances.get(player_id, []) if start <= d <= end)

    def team_roles(self, team: str) -> dict[str, dict]:
        return self.roles.get(names.team(team), {})

    def ice_time(self, player_id: int, as_of: date) -> IceTime:
        return self.ice.get(player_id, IceTime())

    def rostered_trend(self, norm_name: str, as_of: date) -> tuple[float, float, int] | None:
        return self.rostered.get(norm_name)

    def keeper_ranks(self) -> list[dict]:
        return self.keeper

    def lineup_posts(self, days: int) -> list[dict]:
        return self.posts

    def last_name_teams(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for p in self.players.values():
            out.setdefault(names.last(p.name), []).append(p.team)
        return out

    def strategy(self, league: str) -> dict | None:
        return self.strategies.get(league)
