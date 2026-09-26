"""League state as the engine receives it.

This is source-agnostic on purpose: it can come from our own Yahoo sync once
Yahoo approves API access, or be assembled by Claude from a read-only connector
such as Flaim in the meantime.
"""

from __future__ import annotations

from datetime import date
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
    tag: Tag | None = Field(
        default=None,
        description="core = never drop, hold = drop only for a clear upgrade, stream = drop freely. "
        "Stored tags on the server are used when this is empty.",
    )
    nhl_id: int | None = Field(default=None, description="NHL player id if known")
    rank: int | None = Field(default=None, description="Fantasy site rank, if available")
    preseason_rank: int | None = None

    @property
    def is_goalie(self) -> bool:
        return "G" in [p.upper() for p in self.positions]

    @property
    def is_out(self) -> bool:
        return (self.status or "").upper() in OUT_STATUSES


class FreeAgentIn(PlayerIn):
    availability: Literal["FA", "W"] = Field(default="FA", description="FA = free agent, W = on waivers")
    waiver_clears: date | None = Field(default=None, description="Date the player clears waivers, if on waivers")
    percent_rostered: float | None = None


class TeamIn(BaseModel):
    name: str
    players: list[PlayerIn]
    totals: dict[str, float] = Field(
        default_factory=dict,
        description="Category totals already banked this week, keyed like the league categories "
        "(G, A, SOG, SV%, GAA, ...). Optional goalie components: SV, SA, GA, MIN, GS.",
    )


class LeagueIn(BaseModel):
    name: str
    categories: list[str] = Field(description="Scoring categories, e.g. ['G','A','SOG','PPP','BLK','HIT','W','SV%','GAA','SO']")
    roster_slots: dict[str, int] = Field(
        description="Active and bench slots, e.g. {'C':2,'LW':2,'RW':2,'D':4,'Util':1,'G':2,'BN':4,'IR':2}"
    )
    week_start: date
    week_end: date
    today: date | None = Field(default=None, description="First day still to play; defaults to today (US Eastern)")
    max_weekly_adds: int | None = None
    adds_used: int = 0
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
