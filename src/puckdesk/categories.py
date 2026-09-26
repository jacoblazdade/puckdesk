"""Scoring categories: how each one is counted, simulated and compared.

Keys follow Yahoo's display names (G, A, SOG, PPP, SV%, ...). A league lists the
keys it uses; everything else is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    kind: str  # "skater" or "goalie"
    stat: str | None = None  # per-game stat field for counting categories
    ratio: str | None = None  # "sv_pct" or "gaa" for ratio categories
    lower_is_better: bool = False
    # Negative-binomial shape per player-game; None means Poisson.
    # Smaller = lumpier (PIM is very lumpy, shots are close to Poisson).
    dispersion: float | None = None
    # Weight of the last 14 days relative to earlier games this season.
    # Role-driven stats follow deployment, so recent games count extra.
    recent_weight: float = 1.0
    # How many games' worth of weight last season's rate gets. Noisy stats
    # (goals, PIM) lean hard on it; role-driven stats move off it quickly.
    prior_games: float = 12.0
    # Typical per-game value for a rostered skater, used to scale lineup value.
    typical: float = 1.0
    normal: bool = False  # simulate with a normal distribution (can go negative)


SKATER_CATEGORIES: dict[str, Category] = {
    "G": Category("G", "skater", "goals", prior_games=25, typical=0.30),
    "A": Category("A", "skater", "assists", prior_games=25, typical=0.45),
    "P": Category("P", "skater", "points", prior_games=25, typical=0.75),
    "PPP": Category("PPP", "skater", "pp_points", recent_weight=1.5, prior_games=12, typical=0.20),
    "SHP": Category("SHP", "skater", "sh_points", prior_games=40, typical=0.02),
    "GWG": Category("GWG", "skater", "gwg", prior_games=40, typical=0.05),
    "SOG": Category("SOG", "skater", "shots", dispersion=12.0, recent_weight=1.5, prior_games=10, typical=2.6),
    "HIT": Category("HIT", "skater", "hits", dispersion=4.0, recent_weight=1.5, prior_games=8, typical=1.6),
    "BLK": Category("BLK", "skater", "blocks", dispersion=4.0, recent_weight=1.5, prior_games=8, typical=1.2),
    "PIM": Category("PIM", "skater", "pim", dispersion=0.5, prior_games=30, typical=0.6),
    "FW": Category("FW", "skater", "faceoff_wins", dispersion=6.0, recent_weight=1.5, prior_games=8, typical=4.0),
    "+/-": Category("+/-", "skater", "plus_minus", prior_games=40, typical=0.8, normal=True),
}

# Every per-game skater stat the store keeps, whether or not a league scores it.
SKATER_STATS = [
    "goals", "assists", "points", "pp_points", "sh_points", "gwg",
    "shots", "hits", "blocks", "pim", "faceoff_wins", "plus_minus",
]

GOALIE_CATEGORIES: dict[str, Category] = {
    "W": Category("W", "goalie", "wins"),
    "GS": Category("GS", "goalie", "starts"),
    "SV": Category("SV", "goalie", "saves"),
    "SA": Category("SA", "goalie", "shots_against"),
    "GA": Category("GA", "goalie", "goals_against", lower_is_better=True),
    "SO": Category("SO", "goalie", "shutouts"),
    "SV%": Category("SV%", "goalie", ratio="sv_pct"),
    "GAA": Category("GAA", "goalie", ratio="gaa", lower_is_better=True),
}

CATEGORIES: dict[str, Category] = {**SKATER_CATEGORIES, **GOALIE_CATEGORIES}

ALIASES = {
    "PTS": "P",
    "POINTS": "P",
    "GOALS": "G",
    "ASSISTS": "A",
    "SHOTS": "SOG",
    "S": "SOG",
    "HITS": "HIT",
    "BLOCKS": "BLK",
    "BS": "BLK",
    "FOW": "FW",
    "SV PCT": "SV%",
    "SVPCT": "SV%",
    "SAVES": "SV",
    "WINS": "W",
    "SHO": "SO",
    "PLUS/MINUS": "+/-",
    "PM": "+/-",
}


def resolve(key: str) -> Category:
    """Look up a category by Yahoo display name or a common alias."""
    k = key.strip().upper()
    k = ALIASES.get(k, k)
    if k in CATEGORIES:
        return CATEGORIES[k]
    raise KeyError(f"Unknown scoring category: {key!r}")


def split(keys: list[str]) -> tuple[list[Category], list[Category]]:
    cats = [resolve(k) for k in keys]
    return [c for c in cats if c.kind == "skater"], [c for c in cats if c.kind == "goalie"]
