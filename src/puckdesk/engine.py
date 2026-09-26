"""Matchup outlook, add/drop planning and the morning digest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from . import categories
from .data import DataSource
from .models import LeagueState
from .projection import Proj, Projector, Usage
from .simulate import CatResult, compare, simulate_team

EASTERN = ZoneInfo("America/New_York")
LIGHT_NIGHT_MAX_GAMES = 8
MIN_GAIN = 0.03  # expected category wins; below this a move is noise
RESERVE_BASE = 0.10  # value of keeping an add in hand on the first day of the week


def today_eastern() -> date:
    return datetime.now(EASTERN).date()


@dataclass
class Evaluation:
    results: list[CatResult]
    expected: float
    usage: Usage


@dataclass
class Move:
    add: Proj
    drop: Proj
    gain: float
    before: dict[str, float]
    after: dict[str, float]
    next_week_add: int
    next_week_drop: int
    drop_games_left: int
    uses_last_add: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        changes = {
            k: {"before": round(self.before[k], 2), "after": round(self.after[k], 2)}
            for k in self.before
            if abs(self.after[k] - self.before[k]) >= 0.02
        }
        fa = self.add.source
        return {
            "add": {
                "name": self.add.name,
                "team": self.add.team,
                "positions": self.add.positions,
                "games_left": self.add.games_left,
                "next_week_games": self.next_week_add,
                "availability": getattr(fa, "availability", "FA"),
                "waiver_clears": str(fa.waiver_clears) if getattr(fa, "waiver_clears", None) else None,
                "percent_rostered": getattr(fa, "percent_rostered", None),
                "start_share": round(self.add.grates.start_share, 2) if self.add.grates else None,
            },
            "drop": {
                "name": self.drop.name,
                "team": self.drop.team,
                "tag": self.drop.tag,
                "games_left": self.drop_games_left,
                "next_week_games": self.next_week_drop,
            },
            "gain_expected_categories": round(self.gain, 3),
            "category_changes": changes,
            "uses_last_add": self.uses_last_add,
            "confirm_goalie_start": bool(self.add.goalie and self.add.grates and self.add.grates.start_share < 0.9),
            "reasons": self.reasons,
        }


class Engine:
    def __init__(self, data: DataSource, state: LeagueState, n_sims: int = 10000, seed: int = 7):
        self.data = data
        self.state = state
        self.league = state.league
        self.as_of = self.league.today or today_eastern()
        self.cats = [categories.resolve(k) for k in self.league.categories]
        self.skater_cats = [c for c in self.cats if c.kind == "skater"]
        self.n_sims = n_sims
        self.seed = seed
        self.projector = Projector(data, self.league, self.as_of, self.skater_cats)
        self.me = [self.projector.player(p) for p in state.my_team.players]
        self.opp = [self.projector.player(p) for p in state.opponent.players]
        self._opp_cache: dict[int, tuple] = {}

    # --- evaluation -------------------------------------------------------------
    def _opp(self, n: int):
        if n not in self._opp_cache:
            usage = self.projector.usage(self.opp)
            rng = np.random.default_rng(self.seed + 1)
            self._opp_cache[n] = (usage, simulate_team({p.key: p for p in self.opp}, usage, self.skater_cats, n, rng))
        return self._opp_cache[n]

    def evaluate(self, roster: list[Proj], n: int | None = None) -> Evaluation:
        n = n or self.n_sims
        usage = self.projector.usage(roster)
        rng = np.random.default_rng(self.seed)  # same draws every call, so moves compare fairly
        sim = simulate_team({p.key: p for p in roster}, usage, self.skater_cats, n, rng)
        _, opp_sim = self._opp(n)
        results = compare(self.cats, self.state.my_team.totals, sim, self.state.opponent.totals, opp_sim)
        return Evaluation(results, sum(r.expected for r in results), usage)

    def matchup(self) -> dict:
        ev = self.evaluate(self.me)
        opp_usage, _ = self._opp(self.n_sims)
        return {
            "expected_category_wins": round(ev.expected, 2),
            "categories_total": len(self.cats),
            "categories": [
                {
                    "key": r.key,
                    "now": [r.now_me, r.now_opp],
                    "projected": [_round(r.proj_me, r.key), _round(r.proj_opp, r.key)],
                    "p_win": round(r.p_win, 3),
                    "p_tie": round(r.p_tie, 3),
                    "p_loss": round(r.p_loss, 3),
                    "status": r.status,
                }
                for r in ev.results
            ],
            "games_left": {
                "me": round(sum(ev.usage.skater_games.values()), 1),
                "opp": round(sum(opp_usage.skater_games.values()), 1),
            },
            "goalie_starts_left": {
                "me": round(sum(p for _, p, _, _ in ev.usage.goalie_games), 1),
                "opp": round(sum(p for _, p, _, _ in opp_usage.goalie_games), 1),
            },
            "window": {"from": str(self.as_of), "to": str(self.league.week_end)},
        }

    # --- moves --------------------------------------------------------------------
    def adds_left(self) -> int | None:
        if self.league.max_weekly_adds is None:
            return None
        return max(self.league.max_weekly_adds - self.league.adds_used, 0)

    def reserve_value(self, adds_left: int | None) -> float:
        if adds_left is None:
            return 0.0
        days_left = max((self.league.week_end - self.as_of).days, 0)
        week_len = max((self.league.week_end - self.league.week_start).days, 1)
        value = RESERVE_BASE * days_left / week_len  # unused adds are worthless by the last day
        return value * (1.5 if adds_left <= 1 else 1.0)

    def _next_week(self, team: str) -> int:
        s = self.league.week_end + timedelta(days=1)
        return len(self.data.team_dates(team, s, s + timedelta(days=6)))

    def plan_moves(self, max_moves: int = 3, screen_n: int = 3000) -> dict:
        adds_left = self.adds_left()
        limit = max_moves if adds_left is None else min(max_moves, adds_left)
        out: dict = {
            "adds": {"max": self.league.max_weekly_adds, "used": self.league.adds_used, "left": adds_left},
            "moves": [],
            "skipped": [],
        }
        if limit <= 0:
            out["skipped"].append("No adds left this week.")
            return out

        pool = [self.projector.free_agent(fa) for fa in self.state.free_agents]
        pool = [p for p in pool if p.avail > 0 and p.games_left > 0]
        skaters = sorted((p for p in pool if not p.goalie), key=lambda p: p.games_left * p.value, reverse=True)[:25]
        goalies = sorted(
            (p for p in pool if p.goalie), key=lambda p: p.games_left * p.grates.start_share, reverse=True
        )[:6]
        candidates = skaters + goalies

        roster = list(self.me)
        used_add: set[str] = set()
        for _ in range(limit):
            base = self.evaluate(roster, screen_n)
            drops = [p for p in roster if p.tag in ("stream", "hold")]
            if not drops:
                out["skipped"].append("No players tagged Stream or Hold, so nothing can be dropped.")
                break
            scored = []
            for a in candidates:
                if a.key in used_add:
                    continue
                for d in drops:
                    trial = [p for p in roster if p.key != d.key] + [a]
                    scored.append((self.evaluate(trial, screen_n).expected - base.expected, a, d))
            scored.sort(key=lambda t: t[0], reverse=True)

            base_full = self.evaluate(roster)
            reserve = self.reserve_value(None if adds_left is None else adds_left - len(out["moves"]))
            best = None
            for _, a, d in scored[:8]:
                trial = [p for p in roster if p.key != d.key] + [a]
                ev = self.evaluate(trial)
                gain = ev.expected - base_full.expected
                if d.tag == "hold" and gain < self.league.hold_threshold:
                    continue
                if gain < max(MIN_GAIN, reserve):
                    continue
                if best is None or gain > best[0]:
                    best = (gain, a, d, ev)
            if best is None:
                if not out["moves"]:
                    out["skipped"].append(
                        f"No add beats keeping the add in hand (needs +{max(MIN_GAIN, reserve):.2f} expected categories)."
                    )
                break
            gain, a, d, ev = best
            before = {r.key: r.expected for r in base_full.results}
            after = {r.key: r.expected for r in ev.results}
            left_after = None if adds_left is None else adds_left - len(out["moves"]) - 1
            move = Move(
                add=a,
                drop=d,
                gain=gain,
                before=before,
                after=after,
                next_week_add=self._next_week(a.team),
                next_week_drop=self._next_week(d.team),
                drop_games_left=d.games_left if d.avail > 0 else 0,
                uses_last_add=left_after == 0,
            )
            move.reasons = _reasons(move)
            out["moves"].append(move.as_dict())
            roster = [p for p in roster if p.key != d.key] + [a]
            used_add.add(a.key)
        return out

    # --- context for the digest ------------------------------------------------------
    def protected(self) -> list[dict]:
        """Core players who look cold but whose underlying volume is intact."""
        out = []
        for p in self.me:
            if p.tag != "core" or p.goalie or not p.ref:
                continue
            season, recent, prior = self.data.skater_lines(p.ref.id, self.as_of)
            if not prior or prior.gp < 20 or season.gp < 4:
                continue
            pts, pts0 = season.rate("points") or 0, prior.rate("points") or 0
            sog, sog0 = season.rate("shots") or 0, prior.rate("shots") or 0
            if pts0 <= 0 or sog0 <= 0:
                continue
            if pts < 0.6 * pts0 and sog >= 0.85 * sog0:
                g, s = season.sums.get("goals", 0), season.sums.get("shots", 0)
                g0, s0 = prior.sums.get("goals", 0), prior.sums.get("shots", 0)
                out.append(
                    {
                        "name": p.name,
                        "team": p.team,
                        "points_per_game": round(pts, 2),
                        "points_per_game_last_season": round(pts0, 2),
                        "shots_per_game": round(sog, 2),
                        "shots_per_game_last_season": round(sog0, 2),
                        "shooting_pct": round(g / s, 3) if s else None,
                        "shooting_pct_last_season": round(g0 / s0, 3) if s0 else None,
                        "expected_goals_at_normal_finishing": round(s * g0 / s0, 1) if s0 else None,
                        "goals": g,
                        "verdict": "hold: volume is intact, finishing should return",
                    }
                )
        return out

    def regression_watch(self) -> list[dict]:
        """Your players scoring on an unsustainable share of their shots."""
        out = []
        for p in self.me:
            if p.goalie or not p.ref:
                continue
            season, _, prior = self.data.skater_lines(p.ref.id, self.as_of)
            g, s = season.sums.get("goals", 0), season.sums.get("shots", 0)
            if g < 3 or s <= 0:
                continue
            pct = g / s
            base = 0.10
            if prior and prior.sums.get("shots", 0) >= 50:
                base = prior.sums.get("goals", 0) / prior.sums["shots"]
            if pct >= max(2 * base, 0.20):
                out.append(
                    {
                        "name": p.name,
                        "team": p.team,
                        "tag": p.tag,
                        "goals": g,
                        "shots": s,
                        "shooting_pct": round(pct, 3),
                        "normal_shooting_pct": round(base, 3),
                        "verdict": "scoring well above his normal rate; likely to cool off",
                    }
                )
        return out

    def next_week(self) -> dict:
        s = self.league.week_end + timedelta(days=1)
        e = s + timedelta(days=6)
        per_date = self.data.games_per_date(s, e)
        light = {d for d, n in per_date.items() if 0 < n <= LIGHT_NIGHT_MAX_GAMES}
        teams = self.data.all_team_dates(s, e)
        rows = [
            {"team": t, "games": len(ds), "light_night_games": sum(1 for d in ds if d in light), "dates": [str(d) for d in ds]}
            for t, ds in teams.items()
        ]
        rows.sort(key=lambda r: (r["games"], r["light_night_games"]), reverse=True)
        return {
            "from": str(s),
            "to": str(e),
            "games_per_date": {str(d): n for d, n in sorted(per_date.items())},
            "light_nights": sorted(str(d) for d in light),
            "teams": rows,
        }

    def digest(self, max_moves: int = 3) -> dict:
        notes = sorted({f"{p.name}: {n}" for p in self.me + self.opp for n in p.notes})
        return {
            "league": self.league.name,
            "opponent": self.state.opponent.name,
            "generated_for": str(self.as_of),
            "matchup": self.matchup(),
            "moves": self.plan_moves(max_moves=max_moves),
            "protected": self.protected(),
            "regression_watch": self.regression_watch(),
            "next_week": self.next_week(),
            "tags": {p.name: p.tag for p in self.me},
            "notes": notes,
        }


def _round(v: float, key: str) -> float | None:
    if v != v:  # NaN
        return None
    if key == "SV%":
        return round(v, 3)
    if key == "GAA":
        return round(v, 2)
    return round(v, 1)


def _reasons(m: Move) -> list[str]:
    r = []
    if m.add.goalie:
        r.append(f"{m.add.name} projects to start about {m.add.grates.start_share:.0%} of {m.add.team}'s games")
    r.append(f"{m.add.games_left} games left for {m.add.name} vs {m.drop_games_left} for {m.drop.name}")
    deltas = sorted(((m.after[k] - m.before[k], k) for k in m.before), reverse=True)
    ups = [f"{k} {m.before[k]:.0%} to {m.after[k]:.0%}" for d, k in deltas if d >= 0.02][:3]
    if ups:
        r.append("Raises win chance in " + ", ".join(ups))
    downs = [f"{k} {m.before[k]:.0%} to {m.after[k]:.0%}" for d, k in reversed(deltas) if d <= -0.02][:2]
    if downs:
        r.append("Costs a little in " + ", ".join(downs))
    if m.next_week_add != m.next_week_drop:
        r.append(f"Next week: {m.next_week_add} games vs {m.next_week_drop}")
    if m.uses_last_add:
        r.append("Uses your last add of the week")
    return r
