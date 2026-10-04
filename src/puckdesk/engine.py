"""Matchup outlook, add/drop planning and the morning digest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import linear_sum_assignment

from . import appearances, categories, leagues, names
from .data import DataSource
from .leaguestate import EASTERN as ET
from .leaguestate import complete, eastern
from .models import LeagueState, TeamIn
from .projection import SLOT_ACCEPTS, Proj, Projector, Usage
from .simulate import CatResult, GoalieMinimum, compare, simulate_team
from .value import Asset, Valuer

EASTERN = ZoneInfo("America/New_York")
LIGHT_NIGHT_MAX_GAMES = 8
MIN_GAIN = 0.03  # expected category wins; below this a move is noise
SHORT_CHANGE = 0.05  # change in the chance of missing the goalie minimum worth mentioning
# Move score = week weight x change in expected category wins + asset weight x change in asset value.
WEIGHTS = {"win_now": (1.0, 0.0), "balanced": (0.6, 0.5), "rebuild": (0.2, 1.0)}
NEAR_TIE = 0.05  # moves this close count as the same; the drop with less long-term value goes
SAME_POSITION_MARGIN = 0.10  # a drop at the add's position wins when it scores at most this much lower
REGULAR_PENALTY = 0.05  # in a near tie, keep a player in an active slot over a bench player
LOW_VALUE = 0.2  # asset value of a replaceable player; rebuild streams goalies only over these
SELL_HIGH_ROSTERED_JUMP = 15.0
BREAKOUT_MAX_ROSTERED = 30.0
RESERVE_BASE = 0.10  # value of keeping an add in hand on the first day of the week


def today_eastern() -> date:
    return datetime.now(EASTERN).date()


@dataclass
class Evaluation:
    results: list[CatResult]
    expected: float
    usage: Usage
    p_short: float | None = None  # chance of ending the week below the goalie minimum


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
    short_before: float | None = None
    short_after: float | None = None
    score: float | None = None
    asset_add: Asset | None = None
    asset_drop: Asset | None = None
    tie_note: str | None = None
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
            "goalie_minimum_risk": None if self.short_before is None else {
                "before": round(self.short_before, 3), "after": round(self.short_after, 3)},
            "score": None if self.score is None else round(self.score, 3),
            "asset_value": {"add": self.asset_add.as_dict(), "drop": self.asset_drop.as_dict()}
            if self.asset_add and self.asset_drop else None,
            "reasons": self.reasons,
        }


class Engine:
    def __init__(self, data: DataSource, state: LeagueState, n_sims: int = 10000, seed: int = 7,
                 now: datetime | None = None):
        self.data = data
        self.state, self.prep_notes = complete(state, now)
        self.league = self.state.league
        self.now = eastern(now) if now else datetime.now(EASTERN)
        self.as_of = self.league.today or (now.date() if now else today_eastern())
        self.cats = [categories.resolve(k) for k in self.league.categories]
        self.labels = {categories.resolve(k).key: k for k in self.league.categories}  # SO -> SHO, as Yahoo shows it
        self.skater_cats = [c for c in self.cats if c.kind == "skater"]
        self.n_sims = n_sims
        self.seed = seed
        self.projector = Projector(data, self.league, self.as_of, self.skater_cats)
        self.live, self.live_error = self._live_games()
        self.projector.started = {
            (names.team(t), g["date"]) for g in self.live if g["date"] >= self.as_of for t in (g["home"], g["away"])
        }
        stored = data.strategy(self.league.name) if hasattr(data, "strategy") else None
        self.strategy = leagues.strategy_for(self.league.name, stored)
        self.weights = WEIGHTS[self.strategy["strategy"]]
        self.valuer = Valuer(data, self.as_of, len(self.skater_cats))
        self.me = [self.projector.player(p) for p in self.state.my_team.players]
        self.opp = [self.projector.player(p) for p in self.state.opponent.players]
        self._opp_cache: dict[int, tuple] = {}
        capacity = sum(n for k, n in self.league.roster_slots.items() if not k.upper().startswith(("IR", "IL")))
        self.open_spots = capacity - sum(1 for p in self.me if not p.source.in_ir_slot) if capacity else 99
        self.goalie_min: GoalieMinimum | None = None
        self.apps_source = None
        if self.league.min_goalie_appearances and any(c.kind == "goalie" for c in self.cats):
            me = self._appearances(self.state.my_team, self.me, mine=True)
            opp = self._appearances(self.state.opponent, self.opp, mine=False)
            self.goalie_min = GoalieMinimum(self.league.min_goalie_appearances, me.used, opp.used)
            self.apps_source = {"me": me.breakdown, "opp": opp.breakdown}

    def _live_games(self) -> tuple[list[dict], str | None]:
        """Started NHL games yesterday and today (Eastern): finished but not ingested, or still going."""
        if not hasattr(self.data, "live_games"):
            return [], None
        try:
            return self.data.live_games([self.as_of - timedelta(days=1), self.as_of]), None
        except Exception as e:  # noqa: BLE001 - box scores and the Yahoo floor still work without it
            self.prep_notes.append(f"Live NHL scores unavailable ({e}); goalie appearances from box scores "
                                   "and the Yahoo totals only.")
            return [], str(e)

    def _appearances(self, team: TeamIn, projs: list[Proj], mine: bool) -> appearances.Banked:
        """Goalie appearances banked this week; see appearances.py."""
        week_start = datetime.combine(self.league.week_start, datetime.min.time(), ET)
        goalies = appearances.goalies_for(
            team, projs, self.state.transactions or [], mine, leagues.find(self.league.name),
            lambda name, nhl_team: self.data.find_player(name, nhl_team, True), week_start, self.now,
        )
        b = appearances.banked(self.data, goalies, self.live, team.totals, team.goalie_appearances, week_start,
                               self.now, self.live_error, " (mine)" if mine else f" ({team.name})")
        if b.note:
            self.prep_notes.append(b.note)
        # With no goals against there's no GAA to derive minutes from; count 60 per appearance.
        if b.used and "MIN" not in team.totals and "GS" not in team.totals:
            team.totals["MIN"] = 60.0 * b.used
        return b

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
        results = compare(self.cats, self.state.my_team.totals, sim, self.state.opponent.totals, opp_sim, self.goalie_min)
        for r in results:
            r.key = self.labels.get(r.key, r.key)
        p_short = None
        if self.goalie_min:
            p_short = float(self.goalie_min.short(self.goalie_min.banked_me, sim).mean())
        return Evaluation(results, sum(r.expected for r in results), usage, p_short)

    def goalie_minimum(self, ev: Evaluation) -> dict | None:
        if not self.goalie_min:
            return None
        g = self.goalie_min
        opp_usage, opp_sim = self._opp(self.n_sims)
        return {
            "required": g.required,
            "me": {
                "so_far": g.banked_me,
                "projected": round(g.banked_me + sum(p for _, p, _, _ in ev.usage.goalie_games), 1),
                "p_short": round(ev.p_short, 3),
            },
            "opp": {
                "so_far": g.banked_opp,
                "projected": round(g.banked_opp + sum(p for _, p, _, _ in opp_usage.goalie_games), 1),
                "p_short": round(float(g.short(g.banked_opp, opp_sim).mean()), 3),
            },
            "so_far_from": self.apps_source,
            "rule": "A team below the minimum can't win any goalie category this week.",
        }

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
            "goalie_minimum": self.goalie_minimum(ev),
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

    # --- asset value and drop rules ---------------------------------------------------
    def asset(self, p: Proj) -> Asset:
        return self.valuer.asset(p, p.source.percent_rostered)

    def _score(self, gain: float, a: Proj, d: Proj) -> float:
        wk, wa = self.weights
        return wk * gain + (wa * (self.asset(a).value - self.asset(d).value) if wa else 0.0)

    def _ir_blocked(self, p: Proj) -> bool:
        """In an IR+ slot with a full roster: dropping him frees no roster spot, so the add can't happen."""
        return p.source.in_ir_slot and self.open_spots <= 0

    def _allowed(self, a: Proj, d: Proj) -> bool:
        st = self.strategy["strategy"]
        if st in ("rebuild", "balanced"):
            da, aa = self.asset(d), self.asset(a)
            if da.young_upside and aa.value <= da.value:
                return False  # never a young upside player for a short-term stream
            if st == "rebuild" and a.goalie and da.value > LOW_VALUE and aa.value < da.value:
                return False  # goalie streams only when they cost no long-term value
        return True

    def _keep_value(self, d: Proj) -> float:
        slot = (d.source.slot or "").upper()
        regular = bool(slot) and slot not in ("BN",) and not d.source.in_ir_slot
        return self.asset(d).value + (REGULAR_PENALTY if regular else 0.0)

    def _active_slots(self) -> list[str]:
        slots: list[str] = []
        for name, count in self.league.roster_slots.items():
            k = name.upper()
            if k in SLOT_ACCEPTS or k == "G":
                slots += [k] * count
        return slots

    def fillable(self, roster: list[Proj]) -> int:
        """How many active slots (C, LW, RW, D, Util, G, ...) the roster can fill at once, counting every
        position a player is eligible at. Players parked in IR slots don't count."""
        slots = self._active_slots()
        players = [p for p in roster if not p.source.in_ir_slot]
        if not slots or not players:
            return 0
        fits = np.array([[1.0 if (p.goalie if s == "G" else not p.goalie and set(p.positions) & SLOT_ACCEPTS[s])
                          else 0.0 for s in slots] for p in players])
        rows, cols = linear_sum_assignment(fits, maximize=True)
        return int(fits[rows, cols].sum())

    def _break_tie(self, best: tuple, full: list[tuple]) -> tuple[tuple, str | None]:
        """Pick the drop for the chosen add: one at the add's position when it scores within
        SAME_POSITION_MARGIN of the best, then, among drops within NEAR_TIE, the one worth least long-term."""
        group = [t for t in full if t[2] is best[2] and best[0] - t[0] <= SAME_POSITION_MARGIN]
        same = [t for t in group if set(t[2].positions) & set(t[3].positions)]
        pool = same or [t for t in group if best[0] - t[0] <= NEAR_TIE]
        top = max(pool, key=lambda t: t[0])
        ties = [t for t in pool if top[0] - t[0] <= NEAR_TIE]
        pick = min(ties, key=lambda t: self._keep_value(t[3]))
        if pick is best:
            return best, None
        d0, d1 = best[3], pick[3]
        if same and not set(best[2].positions) & set(d0.positions):
            note = (f"Drops {d1.name} rather than {d0.name}: same position as {pick[2].name}, and nearly the "
                    f"same score ({pick[0]:+.2f} vs {best[0]:+.2f})")
        else:
            note = (f"Drops {d1.name} rather than {d0.name}: nearly the same gain (+{pick[1]:.2f} vs +{best[1]:.2f}), "
                    f"and {d0.name} has more long-term value ({self.asset(d0).value:.2f} vs {self.asset(d1).value:.2f})")
        return pick, note

    def plan_moves(self, max_moves: int = 3, screen_n: int = 3000) -> dict:
        adds_left = self.adds_left()
        limit = max_moves if adds_left is None else min(max_moves, adds_left)
        wk, wa = self.weights
        out: dict = {
            "adds": {"max": self.league.max_weekly_adds, "used": self.league.adds_used, "left": adds_left},
            "strategy": self.strategy["strategy"],
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
        if wa:
            # Keeper leagues: also the best long-term assets, whatever their games this week.
            ranked = sorted((p for p in pool if not p.goalie), key=lambda p: self.asset(p).value, reverse=True)
            candidates += [p for p in ranked if p not in candidates][:10]

        roster = list(self.me)
        blocked = [p.name for p in roster if self._ir_blocked(p) and p.tag in ("stream", "hold")]
        if blocked:
            out["skipped"].append(
                f"Not dropping {', '.join(blocked)}: IR+ slot with a full roster, so the drop frees no spot for an add."
            )
        used_add: set[str] = set()
        holes: dict[str, str] = {}
        for _ in range(limit):
            base = self.evaluate(roster, screen_n)
            drops = [p for p in roster if p.tag in ("stream", "hold") and not self._ir_blocked(p)]
            if not drops:
                out["skipped"].append("No players tagged Stream or Hold that can be dropped.")
                break
            scored = []
            can_fill = self.fillable(roster)
            for a in candidates:
                if a.key in used_add:
                    continue
                for d in drops:
                    if not self._allowed(a, d):
                        continue
                    trial = [p for p in roster if p.key != d.key] + [a]
                    if self.fillable(trial) < can_fill:
                        holes.setdefault(d.name, a.name)  # would leave an active slot empty
                        continue
                    gain = self.evaluate(trial, screen_n).expected - base.expected
                    scored.append((self._score(gain, a, d), gain, a, d))
            if not scored:
                break
            scored.sort(key=lambda t: t[0], reverse=True)
            shortlist = scored[:8]
            lead = scored[0][2]  # its other drops get a full look too, for the near-tie rule
            shortlist += [t for t in scored[8:] if t[2] is lead and t[0] >= scored[0][0] - 3 * NEAR_TIE][:6]

            base_full = self.evaluate(roster)
            reserve = wk * self.reserve_value(None if adds_left is None else adds_left - len(out["moves"]))
            need = max(MIN_GAIN, reserve)
            full = []
            for _, _, a, d in shortlist:
                ev = self.evaluate([p for p in roster if p.key != d.key] + [a])
                gain = ev.expected - base_full.expected
                score = self._score(gain, a, d)
                if d.tag == "hold" and score < self.league.hold_threshold:
                    continue
                if score < need:
                    continue
                full.append((score, gain, a, d, ev))
            if not full:
                if not out["moves"]:
                    out["skipped"].append(f"No add beats keeping the add in hand (needs a score of +{need:.2f}).")
                break
            best, tie_note = self._break_tie(max(full, key=lambda t: t[0]), full)
            score, gain, a, d, ev = best
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
                short_before=base_full.p_short,
                short_after=ev.p_short,
                score=score,
                asset_add=self.asset(a),
                asset_drop=self.asset(d),
                tie_note=tie_note,
            )
            move.reasons = _reasons(move, self.goalie_min.required if self.goalie_min else 0, wa > 0)
            out["moves"].append(move.as_dict())
            roster = [p for p in roster if p.key != d.key] + [a]
            used_add.add(a.key)
        if holes:
            out["skipped"].append(
                "Not dropping " + ", ".join(sorted(holes)) + " for an add at another position: it would leave an "
                "active slot with no eligible player."
            )
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

    def _hot_shooting(self, p: Proj) -> tuple[int, int, float, float] | None:
        """(goals, shots, shooting %, normal shooting %) when he's far above his normal rate."""
        if p.goalie or not p.ref:
            return None
        season, _, prior = self.data.skater_lines(p.ref.id, self.as_of)
        g, s = season.sums.get("goals", 0), season.sums.get("shots", 0)
        if g < 3 or s <= 0:
            return None
        pct = g / s
        base = 0.10
        if prior and prior.sums.get("shots", 0) >= 50:
            base = prior.sums.get("goals", 0) / prior.sums["shots"]
        return (int(g), int(s), pct, base) if pct >= max(2 * base, 0.20) else None

    def regression_watch(self) -> list[dict]:
        """Your players scoring on an unsustainable share of their shots."""
        out = []
        for p in self.me:
            hot = self._hot_shooting(p)
            if hot:
                g, s, pct, base = hot
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

    def sell_high(self) -> list[dict]:
        """Rostered players whose value is likely at a peak, each with the reason."""
        out = []
        for p in self.me:
            if p.goalie:
                continue
            a = self.asset(p)
            reasons = []
            hot = self._hot_shooting(p)
            if hot:
                g, s, pct, base = hot
                reasons.append(f"{g} goals on {s} shots ({pct:.0%}) against a normal {base:.0%}")
            if (a.rostered_change or 0) >= SELL_HIGH_ROSTERED_JUMP:
                reasons.append(f"% rostered jumped {a.rostered_change:.0f} points to {a.rostered:.0f}%")
            if a.temporary_pp1:
                reasons.append(a.temporary_pp1)
            if reasons:
                out.append({"name": p.name, "team": p.team, "tag": p.tag, "asset_value": round(a.value, 2),
                            "reasons": reasons})
        return sorted(out, key=lambda r: r["asset_value"], reverse=True)

    def breakout_watch(self, limit: int = 8) -> list[dict]:
        """Free agents whose role is rising while their % rostered is still low."""
        out = []
        for fa in self.state.free_agents:
            p = self.projector.free_agent(fa)
            if p.goalie or p.avail <= 0:
                continue
            a = self.asset(p)
            rising_role = a.top_role or (a.toi_change or 0) >= 1.5
            if not rising_role or (a.rostered is not None and a.rostered >= BREAKOUT_MAX_ROSTERED):
                continue
            out.append({"name": p.name, "team": p.team, "positions": p.positions, "percent_rostered": a.rostered,
                        "availability": fa.availability, "asset_value": round(a.value, 2), "age": a.age,
                        "reasons": a.signals})
        return sorted(out, key=lambda r: r["asset_value"], reverse=True)[:limit]

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

    def league_info(self) -> dict:
        from . import leagues

        cfg = leagues.find(self.league.name)
        info = {
            "name": self.league.name,
            "key": self.league.key,
            "week_start": str(self.league.week_start),
            "week_end": str(self.league.week_end),
            "max_weekly_adds": self.league.max_weekly_adds,
            "adds_used": self.league.adds_used,
            "min_goalie_appearances": self.league.min_goalie_appearances,
            "strategy": self.strategy["strategy"],
            "strategy_note": self.strategy["note"],
            "strategy_source": self.strategy["source"],
        }
        if cfg:
            info["week"] = cfg.week_of(self.as_of)[0]
            info["team"] = cfg.team_name
            info["waivers"] = f"{cfg.waiver_days}-day continual rolling"
        return info

    def digest(self, max_moves: int = 3) -> dict:
        notes = sorted({f"{p.name}: {n}" for p in self.me + self.opp for n in p.notes})
        return {
            "league": self.league.name,
            "league_info": self.league_info(),
            "opponent": self.state.opponent.name,
            "generated_for": str(self.as_of),
            "matchup": self.matchup(),
            "moves": self.plan_moves(max_moves=max_moves),
            "protected": self.protected(),
            "regression_watch": self.regression_watch(),
            "sell_high": self.sell_high(),
            "breakout_watch": self.breakout_watch(),
            "next_week": self.next_week(),
            "tags": {p.name: p.tag for p in self.me},
            "notes": self.prep_notes + notes,
        }


def _round(v: float, key: str) -> float | None:
    if v != v:  # NaN
        return None
    if key == "SV%":
        return round(v, 3)
    if key == "GAA":
        return round(v, 2)
    return round(v, 1)


def _reasons(m: Move, goalie_min: int = 0, asset_weighted: bool = False) -> list[str]:
    r = []
    if m.tie_note:
        r.append(m.tie_note)
    if asset_weighted and m.asset_add and m.asset_drop:
        why = ", ".join(m.asset_add.signals[:3]) or "no standout signals"
        r.append(f"Long-term value {m.asset_add.value:.2f} for {m.add.name} ({why}) vs {m.asset_drop.value:.2f} "
                 f"for {m.drop.name}")
    if goalie_min and m.short_before is not None and m.short_after is not None:
        if m.short_before - m.short_after >= SHORT_CHANGE:
            r.append(f"Secures the {goalie_min}-appearance goalie minimum: chance of falling short "
                     f"{m.short_before:.0%} to {m.short_after:.0%}")
        elif m.short_after - m.short_before >= SHORT_CHANGE:
            r.append(f"Raises the risk of missing the {goalie_min}-appearance goalie minimum: "
                     f"{m.short_before:.0%} to {m.short_after:.0%}")
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
