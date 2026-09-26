"""Per-game rate estimates: this season, weighted toward recent games, pulled
toward last season (or a position baseline for players without one)."""

from __future__ import annotations

from dataclasses import dataclass

from .categories import SKATER_CATEGORIES, SKATER_STATS
from .data import GoalieLine, StatLine

# Rough per-game baselines for skaters with no NHL history (approximate).
FORWARD_BASELINE = {
    "goals": 0.18, "assists": 0.25, "points": 0.43, "pp_points": 0.10, "sh_points": 0.01,
    "gwg": 0.03, "shots": 1.9, "hits": 1.3, "blocks": 0.5, "pim": 0.45,
    "faceoff_wins": 0.3, "plus_minus": 0.0,
}
CENTER_FACEOFF_WINS = 5.0
DEFENSE_BASELINE = {
    "goals": 0.06, "assists": 0.22, "points": 0.28, "pp_points": 0.07, "sh_points": 0.01,
    "gwg": 0.01, "shots": 1.4, "hits": 1.3, "blocks": 1.4, "pim": 0.4,
    "faceoff_wins": 0.0, "plus_minus": 0.0,
}
BASELINE_WEIGHT = 0.5  # a baseline is a guess, so it counts half as much as a real prior

LEAGUE_SV_PCT = 0.900
LEAGUE_SA_PER_START = 28.5
SV_PCT_PRIOR_SHOTS = 800  # save % is noisy: ~800 shots before it speaks for itself
START_SHARE_PRIOR_GAMES = 8


@dataclass
class SkaterRates:
    per_game: dict[str, float]
    gp_season: int
    gp_recent: int
    has_prior: bool


@dataclass
class GoalieRates:
    sv_pct: float
    sa_per_start: float
    start_share: float
    gp_season: int


def _stat_weights(stat: str) -> tuple[float, float]:
    for c in SKATER_CATEGORIES.values():
        if c.stat == stat:
            return c.recent_weight, c.prior_games
    return 1.0, 12.0


def skater_rates(season: StatLine, recent: StatLine, prior: StatLine | None, position: str) -> SkaterRates:
    """Blend this season (recent games weighted up) with last season or a baseline."""
    base = DEFENSE_BASELINE if position == "D" else dict(FORWARD_BASELINE)
    if position == "C":
        base = {**base, "faceoff_wins": CENTER_FACEOFF_WINS}
    per_game: dict[str, float] = {}
    for stat in SKATER_STATS:
        w_recent, n_prior = _stat_weights(stat)
        if prior is not None and prior.gp >= 10:
            p_rate = prior.rate(stat) or 0.0
            p_weight = n_prior
        else:
            p_rate = base[stat]
            p_weight = n_prior * BASELINE_WEIGHT
        older_gp = max(season.gp - recent.gp, 0)
        older_sum = season.sums.get(stat, 0.0) - recent.sums.get(stat, 0.0)
        num = p_weight * p_rate + older_sum + w_recent * recent.sums.get(stat, 0.0)
        den = p_weight + older_gp + w_recent * recent.gp
        per_game[stat] = num / den if den > 0 else p_rate
    return SkaterRates(per_game, season.gp, recent.gp, prior is not None and prior.gp >= 10)


def goalie_rates(season: GoalieLine, prior: GoalieLine | None) -> GoalieRates:
    # Save %: shrink toward last season, then toward league average.
    prior_sv = LEAGUE_SV_PCT
    if prior is not None and prior.shots_against >= 300:
        prior_sv = (prior.saves + LEAGUE_SV_PCT * SV_PCT_PRIOR_SHOTS) / (prior.shots_against + SV_PCT_PRIOR_SHOTS)
    sv = (season.saves + prior_sv * SV_PCT_PRIOR_SHOTS) / (season.shots_against + SV_PCT_PRIOR_SHOTS)

    # Shots against per start: mostly a team trait; shrink toward league average.
    sa = (season.shots_against + LEAGUE_SA_PER_START * 10) / (season.starts + 10)

    # Share of team starts.
    prior_share = 0.5
    if prior is not None and prior.team_games > 0:
        prior_share = min(prior.starts / prior.team_games, 0.95)
    if season.team_games > 0:
        share = (season.starts + prior_share * START_SHARE_PRIOR_GAMES) / (season.team_games + START_SHARE_PRIOR_GAMES)
    else:
        share = prior_share
    return GoalieRates(sv_pct=sv, sa_per_start=sa, start_share=min(max(share, 0.0), 0.95), gp_season=season.gp)
