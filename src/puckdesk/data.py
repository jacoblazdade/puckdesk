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

    # --- building ---------------------------------------------------------
    def add_player(self, pid: int, name: str, team: str, position: str) -> None:
        self.players[pid] = PlayerRef(pid, name, names.team(team), position)

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
