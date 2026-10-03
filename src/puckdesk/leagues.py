"""The static league config (leagues.toml): settings Flaim doesn't return.

Each league has a name (used for tags and stored digests), its Yahoo league
key, Jacob's team id, the scoring categories, roster positions, the weekly add
limit, the waiver period and the weekly goalie-appearance minimum. Weeks run
Monday to Sunday; week 1 ends on `week1_end`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

REPO_FILE = Path(__file__).resolve().parents[2] / "leagues.toml"


@dataclass(frozen=True)
class LeagueConfig:
    name: str
    key: str
    team_id: int
    team_name: str
    categories: list[str]
    roster: dict[str, int]
    max_weekly_adds: int
    waiver_days: int
    min_goalie_appearances: int
    week1_end: date
    keeper: bool = False
    aliases: list[str] = field(default_factory=list)

    def week_of(self, d: date) -> tuple[int, date, date]:
        """(week number, Monday, Sunday) of the scoring week that contains `d`."""
        weeks_after = max(0, -(-(d - self.week1_end).days // 7))  # ceiling, never before week 1
        end = self.week1_end + timedelta(days=7 * weeks_after)
        return weeks_after + 1, end - timedelta(days=6), end

    def is_my_team(self, team: str | int | None, my_team_name: str | None = None) -> bool:
        """Matches a team id (6), a Yahoo team key (477.l.60199.t.6) or the team name."""
        if team is None:
            return False
        t = str(team).strip()
        if t == str(self.team_id) or t.endswith(f".t.{self.team_id}"):
            return True
        names = {self.team_name.casefold()} | ({my_team_name.casefold()} if my_team_name else set())
        return t.casefold() in names

    def summary(self) -> dict:
        return {
            "name": self.name,
            "key": self.key,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "keeper": self.keeper,
            "categories": self.categories,
            "roster": self.roster,
            "max_weekly_adds": self.max_weekly_adds,
            "waivers": f"{self.waiver_days}-day continual rolling",
            "min_goalie_appearances": self.min_goalie_appearances,
        }


def load(path: str | os.PathLike | None = None) -> list[LeagueConfig]:
    p = Path(path or os.environ.get("PUCKDESK_LEAGUES") or REPO_FILE)
    if not p.exists():
        return []
    raw = tomllib.loads(p.read_text())
    out = []
    for lg in raw.get("league", []):
        out.append(
            LeagueConfig(
                name=lg["name"],
                key=lg["key"],
                team_id=int(lg["team_id"]),
                team_name=lg.get("team_name", ""),
                categories=list(lg["categories"]),
                roster={k: int(v) for k, v in lg["roster"].items()},
                max_weekly_adds=int(lg["max_weekly_adds"]),
                waiver_days=int(lg.get("waiver_days", 2)),
                min_goalie_appearances=int(lg.get("min_goalie_appearances", 0)),
                week1_end=lg.get("week1_end", raw["week1_end"]),
                keeper=bool(lg.get("keeper", False)),
                aliases=list(lg.get("aliases", [])),
            )
        )
    return out


def find(name_or_key: str | None, configs: list[LeagueConfig] | None = None) -> LeagueConfig | None:
    """A configured league by name, Yahoo league key or alias (case-insensitive)."""
    if not name_or_key:
        return None
    want = name_or_key.strip().casefold()
    for c in load() if configs is None else configs:
        if want in {c.name.casefold(), c.key.casefold(), *(a.casefold() for a in c.aliases)}:
            return c
    return None


def canonical(name_or_key: str) -> str:
    """The configured league name for a name or key; anything else is returned as given."""
    c = find(name_or_key)
    return c.name if c else name_or_key
