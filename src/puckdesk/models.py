"""League state as the engine receives it.

This is source-agnostic on purpose: it can come from our own Yahoo sync once
Yahoo approves API access, or be assembled by Claude from a read-only connector
such as Flaim in the meantime. For leagues in leagues.toml, the settings Flaim
doesn't return (categories, roster, add limit, waivers, week dates, goalie
minimum) come from there; see leaguestate.complete().
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Tag = Literal["core", "hold", "stream"]

# Statuses that mean the player won't play for now.
OUT_STATUSES = {"O", "OUT", "IR", "IR-LT", "IR-NR", "LTIR", "NA", "SUSP"}


class PlayerIn(BaseModel):
    name: str = Field(description="Player name as the fantasy site shows it")
    team: str = Field(description="NHL team abbreviation, e.g. SEA, MIN, UTA")
    positions: list[str] = Field(description="Eligible positions: C, LW, RW, D or G")
    status: str | None = Field(default=None, description="Injury status: DTD, O, IR, IR-LT, NA or empty")
    slot: str | None = Field(
        default=None, description="Lineup slot the player sits in today (C, LW, Util, G, BN, IR+, ...), from get_roster"
    )
    tag: Tag | None = Field(
        default=None,
        description="core = never drop, hold = drop only for a clear upgrade, stream = drop freely. "
        "Stored tags on the server are used when this is empty.",
    )
    nhl_id: int | None = Field(default=None, description="NHL player id if known")
    percent_rostered: float | None = Field(default=None, description="Yahoo-wide % rostered (get_free_agents, or get_roster if shown)")
    rank: int | None = Field(default=None, description="Fantasy site rank, if available")
    preseason_rank: int | None = None

    @property
    def is_goalie(self) -> bool:
        return "G" in [p.upper() for p in self.positions]

    @property
    def is_out(self) -> bool:
        return (self.status or "").upper() in OUT_STATUSES

    @property
    def in_ir_slot(self) -> bool:
        return (self.slot or "").upper().startswith(("IR", "IL"))


class FreeAgentIn(PlayerIn):
    availability: Literal["FA", "W"] = Field(default="FA", description="FA = free agent, W = on waivers")
    waiver_clears: date | None = Field(default=None, description="Date the player clears waivers, if on waivers")


class TeamIn(BaseModel):
    name: str
    team_id: str | int | None = Field(
        default=None, description="Yahoo team id or key, to match this team's transactions (optional)"
    )
    players: list[PlayerIn]
    totals: dict[str, float] = Field(
        default_factory=dict,
        description="Category totals already banked this week, keyed like the league categories "
        "(G, A, SOG, SV%, GAA, SHO, ...). Optional goalie components: SV, SA, GA, MIN, GS. "
        "GA and SA are display-only stats in Yahoo matchups; pass them anyway.",
    )
    goalie_appearances: int | None = Field(
        default=None,
        description="Goalie appearances banked this week, if the matchup shows them; overrides everything. "
        "Otherwise the server counts NHL games (box scores and live scores) for the team's goalies while "
        "they were rostered, with the Yahoo goalie totals (GA, GAA, SA) as a floor.",
    )


class TransactionIn(BaseModel):
    """One side of a Yahoo transaction from get_transactions. An add/drop is two entries."""

    kind: Literal["add", "drop"]
    player: str
    team: str | None = Field(default=None, description="NHL team abbreviation")
    positions: list[str] = Field(default_factory=list)
    fantasy_team: str | int | None = Field(
        default=None, description="Fantasy team that added or dropped: team id (6), team key or team name"
    )
    at: datetime = Field(description="When it happened; times without a zone are read as US Eastern")


class LeagueIn(BaseModel):
    name: str = Field(description="League name or Yahoo league key; configured leagues are matched by either")
    key: str | None = Field(default=None, description="Yahoo league key, e.g. 477.l.60199")
    categories: list[str] = Field(
        default_factory=list,
        description="Scoring categories, e.g. ['G','A','SOG','PPP','BLK','HIT','W','SV%','GAA','SHO']. "
        "Filled from leagues.toml for configured leagues.",
    )
    roster_slots: dict[str, int] = Field(
        default_factory=dict,
        description="Active and bench slots, e.g. {'C':2,'LW':2,'RW':2,'D':4,'Util':1,'G':2,'BN':4,'IR+':2}. "
        "Filled from leagues.toml for configured leagues.",
    )
    week_start: date | None = None
    week_end: date | None = None
    today: date | None = Field(default=None, description="First day still to play; defaults to today (US Eastern)")
    max_weekly_adds: int | None = None
    adds_used: int = Field(default=0, description="Ignored when transactions are given; they're counted instead")
    waiver_days: int | None = Field(default=None, description="Continual rolling waiver period after a drop")
    min_goalie_appearances: int | None = Field(
        default=None, description="Weekly minimum; below it a team can't win any goalie category"
    )
    adds_effective: Literal["today", "tomorrow"] = Field(
        default="today", description="When a free-agent add starts counting"
    )
    hold_threshold: float = Field(
        default=0.15, description="Minimum gain in expected category wins before a Hold player is suggested as a drop"
    )


class LeagueState(BaseModel):
    league: LeagueIn
    my_team: TeamIn
    opponent: TeamIn
    free_agents: list[FreeAgentIn] = Field(default_factory=list)
    transactions: list[TransactionIn] | None = Field(
        default=None,
        description="Recent league transactions (get_transactions), at least since Monday. "
        "Recent drops become waiver players; my adds since Monday 00:00 ET count as adds used.",
    )
