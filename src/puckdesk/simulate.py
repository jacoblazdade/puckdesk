"""Monte Carlo finish to the scoring week: banked totals plus simulated
remaining games, compared category by category."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .categories import Category
from .projection import Proj, Usage

# Chance a starting goalie gets the win, by goals allowed (0, 1, 2, ... 7+).
WIN_BY_GA = np.array([0.93, 0.80, 0.62, 0.42, 0.24, 0.12, 0.06, 0.03])
PLUS_MINUS_VAR_PER_GAME = 1.0


@dataclass
class TeamSim:
    stats: dict[str, np.ndarray]  # rest-of-week totals per stat or goalie component


@dataclass
class CatResult:
    key: str
    now_me: float | None
    now_opp: float | None
    proj_me: float
    proj_opp: float
    p_win: float
    p_tie: float
    p_loss: float

    @property
    def expected(self) -> float:
        return self.p_win + 0.5 * self.p_tie

    @property
    def status(self) -> str:
        if self.p_tie >= 0.5:
            return "likely tie"
        e = self.expected
        if e >= 0.6:
            return "likely win"
        if e <= 0.4:
            return "likely loss"
        return "swing"


def _sample(rng: np.random.Generator, mu: float, var: float, n: int, normal: bool = False) -> np.ndarray:
    if normal:
        return rng.normal(mu, np.sqrt(max(var, 1e-9)), n)
    if mu <= 0:
        return np.zeros(n)
    if var > mu * 1.001:
        shape = mu * mu / (var - mu)
        scale = (var - mu) / mu
        return rng.poisson(rng.gamma(shape, scale, n)).astype(float)
    return rng.poisson(mu, n).astype(float)


def simulate_team(
    projs: dict[str, Proj], usage: Usage, skater_cats: list[Category], n: int, rng: np.random.Generator
) -> TeamSim:
    out: dict[str, np.ndarray] = {}
    for c in skater_cats:
        mu = var = 0.0
        for key, games in usage.skater_games.items():
            p = projs[key]
            r = p.rates.per_game.get(c.stat, 0.0) if p.rates else 0.0
            mu += games * r
            if c.normal:
                var += games * PLUS_MINUS_VAR_PER_GAME
            elif c.dispersion:
                var += games * (r + r * r / c.dispersion)
            else:
                var += games * r
        out[c.stat] = _sample(rng, mu, var, n, normal=c.normal)

    comps = {k: np.zeros(n) for k in ("wins", "saves", "shots_against", "goals_against", "shutouts", "starts", "minutes")}
    for _key, p_start, sv, sa in usage.goalie_games:
        started = rng.random(n) < p_start
        shots = rng.poisson(sa, n) * started
        saves = rng.binomial(shots, sv)
        ga = shots - saves
        win = started & (rng.random(n) < WIN_BY_GA[np.minimum(ga, len(WIN_BY_GA) - 1)])
        comps["starts"] += started
        comps["shots_against"] += shots
        comps["saves"] += saves
        comps["goals_against"] += ga
        comps["wins"] += win
        comps["shutouts"] += win & (ga == 0)
        comps["minutes"] += 60.0 * started
    out.update(comps)
    return TeamSim(out)


def banked_goalie(totals: dict[str, float]) -> dict[str, float]:
    """Goalie components already banked. Uses SV/SA/GA/MIN when given, otherwise
    reconstructs them from SV% and GAA plus starts (approximate)."""
    t = {k.upper(): v for k, v in totals.items()}
    gs = t.get("GS", 0.0)
    sa = t.get("SA")
    sv = t.get("SV")
    ga = t.get("GA")
    mins = t.get("MIN")
    if mins is None:
        mins = 60.0 * gs if gs else None
    if sa is None and sv is not None and t.get("SV%"):
        sa = sv / t["SV%"]
    if sa is None and gs:
        sa = 28.5 * gs
    if sv is None and sa is not None and t.get("SV%") is not None:
        sv = t["SV%"] * sa
    if ga is None and sa is not None and sv is not None:
        ga = sa - sv
    if ga is None and t.get("GAA") is not None and mins:
        ga = t["GAA"] * mins / 60.0
    if mins is None and ga is not None and t.get("GAA"):
        mins = ga * 60.0 / t["GAA"]
    return {
        "saves": sv or 0.0,
        "shots_against": sa or 0.0,
        "goals_against": ga or 0.0,
        "minutes": mins or 0.0,
    }


def _final(c: Category, totals: dict[str, float], sim: TeamSim) -> np.ndarray:
    t = {k.upper(): v for k, v in totals.items()}
    if c.ratio == "sv_pct":
        b = banked_goalie(totals)
        sv = b["saves"] + sim.stats["saves"]
        sa = b["shots_against"] + sim.stats["shots_against"]
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(sa > 0, sv / sa, np.nan)
    if c.ratio == "gaa":
        b = banked_goalie(totals)
        ga = b["goals_against"] + sim.stats["goals_against"]
        mins = b["minutes"] + sim.stats["minutes"]
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(mins > 0, ga * 60.0 / mins, np.nan)
    return t.get(c.key.upper(), 0.0) + sim.stats[c.stat]


def compare(cats: list[Category], me_totals: dict, me_sim: TeamSim, opp_totals: dict, opp_sim: TeamSim) -> list[CatResult]:
    results = []
    for c in cats:
        a = _final(c, me_totals, me_sim)
        b = _final(c, opp_totals, opp_sim)
        if c.ratio:
            digits = 3 if c.ratio == "sv_pct" else 2
            a_r, b_r = np.round(a, digits), np.round(b, digits)
        else:
            a_r, b_r = np.round(a, 1), np.round(b, 1)
        a_nan, b_nan = np.isnan(a_r), np.isnan(b_r)
        both = ~a_nan & ~b_nan
        if c.lower_is_better:
            win = (both & (a_r < b_r)) | (~a_nan & b_nan)
        else:
            win = (both & (a_r > b_r)) | (~a_nan & b_nan)
        tie = (both & (a_r == b_r)) | (a_nan & b_nan)
        n = len(a)
        p_win, p_tie = win.sum() / n, tie.sum() / n
        t_me = {k.upper(): v for k, v in me_totals.items()}
        t_opp = {k.upper(): v for k, v in opp_totals.items()}
        results.append(
            CatResult(
                key=c.key,
                now_me=t_me.get(c.key.upper()),
                now_opp=t_opp.get(c.key.upper()),
                proj_me=float(np.nanmean(a)) if not np.all(np.isnan(a)) else float("nan"),
                proj_opp=float(np.nanmean(b)) if not np.all(np.isnan(b)) else float("nan"),
                p_win=float(p_win),
                p_tie=float(p_tie),
                p_loss=float(1.0 - p_win - p_tie),
            )
        )
    return results
