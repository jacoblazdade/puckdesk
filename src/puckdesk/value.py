"""Asset value: what a player is worth beyond this week, for keeper leagues and
for breaking near-ties between drops.

The score starts from Dobber's Top 300 Keeper League rank (or production when
he's unranked) and adds:
- age: younger players carry more upside;
- role: Daily Faceoff line and power-play unit, plus Game Day Tweets posts
  that put him on the top unit;
- ice time and shot trends against last season, and early production;
- Yahoo-wide % rostered and its trend over about a week;
- Dobber's month-over-month rank change.
Roughly: 1.0 is a top-60 keeper asset, 0.5 a solid young piece, under 0.2 a
replaceable veteran. Each bonus comes with a short reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from . import mentions, names
from .data import IceTime

YOUNG_AGE = 25
TOP_LINES = {"f1", "f2", "d1", "d2"}
ROSTERED_RISING = 5.0  # points over about a week
AGE_BONUS = [(21, 0.25), (23, 0.18), (25, 0.10), (28, 0.0), (31, -0.08), (99, -0.15)]


@dataclass
class Asset:
    value: float
    age: int | None = None
    line: str | None = None
    pp: str | None = None
    top_unit_post: bool = False
    rostered: float | None = None
    rostered_change: float | None = None
    keeper_rank: int | None = None
    toi_change: float | None = None
    temporary_pp1: str | None = None
    signals: list[str] = field(default_factory=list)

    @property
    def top_role(self) -> bool:
        return self.line in TOP_LINES or self.pp == "pp1" or self.top_unit_post

    @property
    def rising(self) -> bool:
        return (self.rostered_change or 0) >= ROSTERED_RISING

    @property
    def young_upside(self) -> bool:
        """25 or younger with a top-6 (top-4 D) or PP1 role, or rising % rostered."""
        return self.age is not None and self.age <= YOUNG_AGE and (self.top_role or self.rising)

    def as_dict(self) -> dict:
        return {"value": round(self.value, 2), "age": self.age, "young_upside": self.young_upside,
                "signals": self.signals}


def age_on(birth: date | None, day: date) -> int | None:
    if not birth:
        return None
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


class Valuer:
    def __init__(self, data, as_of: date, n_skater_cats: int, matcher: mentions.Matcher | None = None):
        self.data = data
        self.as_of = as_of
        self.n_cats = max(n_skater_cats, 1)
        self.matcher = matcher or mentions.Matcher(_call(data, "last_name_teams", default={}))
        self._roles: dict[str, dict] = {}
        rows = _call(data, "keeper_ranks", default=[])
        self.keeper_by_name = {names.person(r["name"]): r for r in rows}
        self.keeper_by_last = {}
        for r in rows:
            self.keeper_by_last.setdefault((names.last(r["name"]), r["team"]), r)
        self.posts = _call(data, "lineup_posts", 3, default=[])
        self._cache: dict[str, Asset] = {}

    def roles(self, team: str) -> dict:
        if team not in self._roles:
            self._roles[team] = _call(self.data, "team_roles", team, default={})
        return self._roles[team]

    def keeper(self, name: str, team: str) -> dict | None:
        return self.keeper_by_name.get(names.person(name)) or self.keeper_by_last.get((names.last(name), team))

    def asset(self, p, rostered: float | None = None) -> Asset:
        """p is a projection.Proj."""
        if p.key in self._cache:
            return self._cache[p.key]
        a = self._goalie(p) if p.goalie else self._skater(p)
        self._rostered(p, a, rostered)
        self._cache[p.key] = a
        return a

    # --- skaters ---------------------------------------------------------------------
    def _skater(self, p) -> Asset:
        a = Asset(0.0)
        k = self.keeper(p.name, p.team)
        if k:
            a.keeper_rank = k["rank"]
            a.value = 1.2 * math.exp(-k["rank"] / 120)
            a.signals.append(f"Dobber keeper rank {k['rank']}")
            if (k.get("change") or 0) >= 15:
                a.value += 0.04
                a.signals.append(f"up {k['change']} spots in Dobber's keeper ranks")
        else:
            a.value = min(0.2, 0.12 * p.value / self.n_cats)

        a.age = age_on(p.ref.birth_date if p.ref else None, self.as_of)
        bonus = next(b for limit, b in AGE_BONUS if (a.age or 27) <= limit)
        a.value += bonus
        if a.age is not None and a.age <= YOUNG_AGE:
            a.signals.append(f"age {a.age}")
        young = 1.25 if a.age is not None and a.age <= YOUNG_AGE else 1.0

        role = self.roles(p.team).get(p.key, {})
        a.line, a.pp = role.get("line"), role.get("pp")
        if a.line in ("f1", "f2"):
            a.value += 0.12 * young
            a.signals.append("top-6 forward")
        elif a.line in ("d1", "d2"):
            a.value += 0.08 * young
            a.signals.append("top-4 defenseman")
        if a.pp == "pp1":
            a.value += 0.15 * young
            a.signals.append("PP1")
        elif a.pp == "pp2":
            a.value += 0.04
        if a.pp != "pp1" and any(mentions.on_top_unit(post["text"], self.matcher, p.name, p.team) for post in self.posts):
            a.top_unit_post = True
            a.value += 0.08 * young
            a.signals.append("on the top PP unit in a recent lineup post")

        ice: IceTime = _call(self.data, "ice_time", p.ref.id, self.as_of, default=IceTime()) if p.ref else IceTime()
        if ice.gp >= 2 and ice.toi is not None and ice.toi_prior:
            a.toi_change = ice.toi - ice.toi_prior
            if a.toi_change >= 2.0:
                a.value += 0.08 * young
                a.signals.append(f"ice time up {a.toi_change:.1f} min a game")
            elif a.toi_change <= -3.0:
                a.value -= 0.05
                a.signals.append(f"ice time down {-a.toi_change:.1f} min a game")
        if ice.gp >= 2 and ice.pp_toi is not None and ice.pp_toi_prior is not None and ice.pp_toi - ice.pp_toi_prior >= 1.0:
            a.value += 0.04 * young
            a.signals.append(f"PP time up {ice.pp_toi - ice.pp_toi_prior:.1f} min a game")

        if p.ref:
            season, _, prior = self.data.skater_lines(p.ref.id, self.as_of)
            pts, pts0 = season.rate("points"), prior.rate("points") if prior else None
            sog, sog0 = season.rate("shots"), prior.rate("shots") if prior else None
            if season.gp >= 3 and prior and prior.gp >= 20:
                if pts is not None and pts0 is not None and pts >= 0.6 and pts >= 1.5 * max(pts0, 0.2):
                    a.value += 0.05
                    a.signals.append(f"{pts:.2f} points a game vs {pts0:.2f} last season")
                if sog is not None and sog0 is not None and sog - sog0 >= 0.7:
                    a.value += 0.04
                    a.signals.append(f"{sog:.1f} shots a game vs {sog0:.1f} last season")
            if a.pp == "pp1" and prior and prior.gp >= 30 and (prior.rate("pp_points") or 0) < 0.15:
                a.temporary_pp1 = (f"PP1 now, but {int(prior.sums.get('pp_points', 0))} PPP in {prior.gp} games "
                                   "last season: the spot may not last")
        return a

    # --- goalies ---------------------------------------------------------------------
    def _goalie(self, p) -> Asset:
        share = p.grates.start_share if p.grates else 0.3
        a = Asset(0.1 + 0.4 * share)
        a.age = age_on(p.ref.birth_date if p.ref else None, self.as_of)
        if a.age is not None and a.age <= YOUNG_AGE and share >= 0.5:
            a.value += 0.1
            a.signals.append(f"age {a.age}, starting {share:.0%} of games")
        return a

    def _rostered(self, p, a: Asset, rostered: float | None) -> None:
        a.rostered = rostered
        trend = _call(self.data, "rostered_trend", p.key, self.as_of, default=None)
        if not trend:
            return
        now, before, days = trend
        if rostered is not None:
            now = rostered
        a.rostered = now
        a.rostered_change = now - before
        if a.rostered_change >= 20:
            a.value += 0.12
            a.signals.append(f"% rostered up {a.rostered_change:.0f} points in {days} days")
        elif a.rostered_change >= 10:
            a.value += 0.08
            a.signals.append(f"% rostered up {a.rostered_change:.0f} points in {days} days")
        elif a.rostered_change <= -10:
            a.value -= 0.04


def _call(data, method: str, *args, default=None):
    fn = getattr(data, method, None)
    if fn is None:
        return default
    try:
        out = fn(*args)
    except Exception:  # noqa: BLE001 - a missing signal never breaks a digest
        return default
    return default if out is None and default is not None else out
