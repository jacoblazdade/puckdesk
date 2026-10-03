"""Turn a fantasy roster into expected active games and per-game rates for the
rest of the scoring week, including daily lineup-slot limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from scipy.optimize import linear_sum_assignment

from . import names
from .categories import Category
from .data import DataSource, PlayerRef
from .models import FreeAgentIn, LeagueIn, PlayerIn
from .rates import GoalieRates, SkaterRates, goalie_rates, skater_rates

DTD_PLAY_PROB = 0.7
# Goalie guess reasons that mean a plain starter; other reasons (back to back, alternating) count less.
STRONG_GUESS = {"starter", "likely", "probable", "expected", "projected"}

# Which fantasy positions fill which lineup slot.
SLOT_ACCEPTS = {
    "C": {"C"},
    "LW": {"LW"},
    "RW": {"RW"},
    "W": {"LW", "RW"},
    "F": {"C", "LW", "RW"},
    "D": {"D"},
    "UTIL": {"C", "LW", "RW", "D"},
}
NHL_POSITION = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}


def clean_positions(positions: list[str]) -> list[str]:
    out = []
    for p in positions:
        u = p.strip().upper()
        u = {"L": "LW", "R": "RW"}.get(u, u)
        if u in {"C", "LW", "RW", "D", "G"} and u not in out:
            out.append(u)
    return out


@dataclass
class Proj:
    key: str
    name: str
    team: str
    positions: list[str]
    goalie: bool
    ref: PlayerRef | None
    rates: SkaterRates | None
    grates: GoalieRates | None
    dates: list[date]
    avail: float
    value: float
    tag: str
    source: PlayerIn
    notes: list[str] = field(default_factory=list)

    @property
    def games_left(self) -> int:
        return len(self.dates)


@dataclass
class Usage:
    skater_games: dict[str, float]  # player key -> expected games in an active slot
    goalie_games: list[tuple[str, float, float, float]]  # (key, start prob, save %, shots against)
    by_date: dict[date, list[str]] = field(default_factory=dict)

    def active_games(self, key: str) -> float:
        g = self.skater_games.get(key, 0.0)
        g += sum(p for k, p, _, _ in self.goalie_games if k == key)
        return g


class Projector:
    def __init__(self, data: DataSource, league: LeagueIn, as_of: date, skater_cats: list[Category]):
        self.data = data
        self.league = league
        self.as_of = as_of
        self.skater_cats = skater_cats
        self.stored_tags = data.tags(league.name)
        # Starting-goalie guesses (Game Day Tweets) override season start shares on their dates.
        hints = data.goalie_hints(as_of, league.week_end) if hasattr(data, "goalie_hints") else []
        self.hint_by_goalie = {(h["game_date"], h["norm_name"]): h["status"] for h in hints}
        self.hint_by_team = {(h["game_date"], names.team(h["team"])): h["norm_name"] for h in hints if h.get("team")}

    def window(self) -> tuple[date, date]:
        return self.as_of, self.league.week_end

    def player(self, p: PlayerIn, start: date | None = None, end: date | None = None) -> Proj:
        positions = clean_positions(p.positions)
        goalie = "G" in positions
        team = names.team(p.team)
        ref = self.data.find_player(p.name, team, goalie)
        notes: list[str] = []
        if ref is None:
            notes.append("not matched to an NHL player; using baseline rates")
        if ref and ref.team and ref.team != team:
            notes.append(f"NHL lists {ref.team}, fantasy site says {team}; using {ref.team}")
            team = ref.team
        s = start or self.as_of
        e = end or self.league.week_end
        dates = self.data.team_dates(team, s, e)
        status = (p.status or "").upper()
        avail = 0.0 if p.is_out else (DTD_PLAY_PROB if status in {"DTD", "GTD", "Q"} else 1.0)
        if p.in_ir_slot and not p.is_out:
            notes.append(f"in the {p.slot} slot without an injury status; move him to an active slot to play")

        rates = grates = None
        if goalie:
            if ref:
                season, prior = self.data.goalie_lines(ref.id, self.as_of)
                grates = goalie_rates(season, prior)
            else:
                from .data import GoalieLine

                grates = goalie_rates(GoalieLine(), None)
            value = grates.start_share
        else:
            main_pos = positions[0] if positions else "LW"
            if ref:
                season, recent, prior = self.data.skater_lines(ref.id, self.as_of)
                nhl_pos = NHL_POSITION.get(ref.position, main_pos)
                rates = skater_rates(season, recent, prior, "C" if nhl_pos == "C" else ("D" if nhl_pos == "D" else "F"))
            else:
                from .data import StatLine

                rates = skater_rates(StatLine(), StatLine(), None, "C" if main_pos == "C" else ("D" if main_pos == "D" else "F"))
            value = sum(
                rates.per_game.get(c.stat, 0.0) / c.typical
                for c in self.skater_cats
                if c.stat and not c.normal and c.typical > 0
            )

        key = names.person(p.name)
        tag = p.tag or self.stored_tags.get(key) or "hold"
        return Proj(key, p.name, team, positions, goalie, ref, rates, grates, dates, avail, value, tag, p, notes)

    def free_agent(self, fa: FreeAgentIn) -> Proj:
        start = self.as_of
        if self.league.adds_effective == "tomorrow":
            start = self.as_of + timedelta(days=1)
        if fa.availability == "W" and fa.waiver_clears and fa.waiver_clears > start:
            start = fa.waiver_clears
        return self.player(fa, start=start)

    def start_prob(self, g: Proj, d: date) -> float:
        """Chance a goalie starts on a date: a posted guess beats the season share."""
        status = self.hint_by_goalie.get((d, g.key))
        if status == "confirmed":
            p = 0.97
        elif status in STRONG_GUESS:
            p = 0.85
        elif status:
            p = 0.75  # a guess with a caveat: back to back, alternating, ...
        elif (d, g.team) in self.hint_by_team:
            p = min(g.grates.start_share, 0.12)  # someone else is expected in net
        else:
            p = g.grates.start_share
        return p * g.avail

    # --- lineups --------------------------------------------------------------
    def usage(self, projs: list[Proj]) -> Usage:
        slots = {k.upper(): v for k, v in self.league.roster_slots.items()}
        skater_slots: list[str] = []
        for name, count in slots.items():
            if name in SLOT_ACCEPTS:
                skater_slots += [name] * count
        g_slots = slots.get("G", 0)

        skater_games: dict[str, float] = {}
        goalie_games: list[tuple[str, float, float, float]] = []
        by_date: dict[date, list[str]] = {}
        start, end = self.window()
        d = start
        while d <= end:
            playing = [p for p in projs if d in p.dates and p.avail > 0]
            skaters = [p for p in playing if not p.goalie]
            goalies = [p for p in playing if p.goalie]
            active: list[str] = []
            if skaters and skater_slots:
                m = np.full((len(skaters), len(skater_slots)), -1e6)
                for i, p in enumerate(skaters):
                    for j, slot in enumerate(skater_slots):
                        if set(p.positions) & SLOT_ACCEPTS[slot]:
                            m[i, j] = p.value * p.avail
                rows, cols = linear_sum_assignment(-m)
                for i, j in zip(rows, cols):
                    if m[i, j] > -1e5:
                        p = skaters[i]
                        skater_games[p.key] = skater_games.get(p.key, 0.0) + p.avail
                        active.append(p.key)
            if goalies and g_slots:
                probs = {g.key: self.start_prob(g, d) for g in goalies}
                goalies.sort(key=lambda g: probs[g.key], reverse=True)
                for g in goalies[:g_slots]:
                    goalie_games.append((g.key, probs[g.key], g.grates.sv_pct, g.grates.sa_per_start))
                    active.append(g.key)
            by_date[d] = active
            d += timedelta(days=1)
        return Usage(skater_games, goalie_games, by_date)
