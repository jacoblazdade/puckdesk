"""Complete a LeagueState assembled from Flaim before the engine sees it.

Flaim (the Yahoo connector in Claude) returns rosters, matchup values, free
agents and transactions, but not league settings, waiver status or week
dates. This fills those in:

- settings for leagues in leagues.toml (categories, roster, add limit,
  waiver period, goalie minimum) and the current Monday-to-Sunday week;
- totals keyed by Yahoo display names (SHO) moved to the engine's keys (SO),
  goalie minutes derived as GA x 60 / GAA when GA > 0, saves as SA - GA;
- from transactions: players dropped within the waiver period are on
  waivers until drop time + waiver period, and my adds since Monday
  00:00 ET are the adds used.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import categories, leagues, names
from .models import FreeAgentIn, LeagueState, TeamIn, TransactionIn

EASTERN = ZoneInfo("America/New_York")
# A waiver player who clears after this time (ET) misses that night's games.
WAIVER_SAME_DAY_CUTOFF = time(18, 0)


def eastern(dt: datetime) -> datetime:
    return dt.replace(tzinfo=EASTERN) if dt.tzinfo is None else dt.astimezone(EASTERN)


def complete(state: LeagueState, now: datetime | None = None) -> tuple[LeagueState, list[str]]:
    """A filled-in copy of the state, plus notes on what was derived."""
    state = state.model_copy(deep=True)
    lg = state.league
    notes: list[str] = []
    now = eastern(now) if now else datetime.now(EASTERN)
    today = lg.today or now.date()

    cfg = leagues.find(lg.key) or leagues.find(lg.name)
    if cfg:
        _, start, end = cfg.week_of(today)
        lg.name, lg.key = cfg.name, cfg.key
        lg.categories = list(cfg.categories)
        lg.roster_slots = dict(cfg.roster)
        lg.max_weekly_adds = cfg.max_weekly_adds
        lg.waiver_days = cfg.waiver_days
        lg.min_goalie_appearances = cfg.min_goalie_appearances
        lg.week_start, lg.week_end = start, end
    missing = [f for f in ("categories", "roster_slots", "week_start", "week_end") if not getattr(lg, f)]
    if missing:
        raise ValueError(
            f"League {lg.name!r} is not in leagues.toml, so the state needs {', '.join(missing)}. "
            f"Configured leagues: {', '.join(c.name for c in leagues.load()) or 'none'}."
        )

    for team in (state.my_team, state.opponent):
        team.totals = normalize_totals(team.totals)

    if state.transactions is not None:
        _apply_transactions(state, cfg, now, notes)
    return state, notes


def normalize_totals(totals: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in totals.items():
        if v is None or v == "" or v == "-":
            continue
        try:
            key = categories.resolve(k).key
        except KeyError:
            key = k.strip().upper()
        out[key] = float(v)
    ga, gaa, sa = out.get("GA"), out.get("GAA"), out.get("SA")
    if "MIN" not in out and ga and gaa:
        out["MIN"] = ga * 60.0 / gaa
    if "SV" not in out and sa is not None and ga is not None:
        out["SV"] = sa - ga
    return out


def _apply_transactions(state: LeagueState, cfg: leagues.LeagueConfig | None, now: datetime, notes: list[str]) -> None:
    lg = state.league
    txs = sorted(state.transactions or [], key=lambda t: eastern(t.at))
    week_start = datetime.combine(lg.week_start, time(0), EASTERN)

    if cfg:
        mine = [t for t in txs if t.kind == "add" and cfg.is_my_team(t.fantasy_team, state.my_team.name)
                and eastern(t.at) >= week_start]
        lg.adds_used = len(mine)
        notes.append(f"Adds used this week from transactions: {lg.adds_used} of {lg.max_weekly_adds}")

    waiver = timedelta(days=lg.waiver_days or (cfg.waiver_days if cfg else 2))
    last: dict[str, TransactionIn] = {}
    for t in txs:
        last[names.person(t.player)] = t
    on_waivers = {
        key: t for key, t in last.items()
        if t.kind == "drop" and eastern(t.at) + waiver > now
    }
    claimed = {key for key, t in last.items() if t.kind == "add"}

    pool: list[FreeAgentIn] = []
    seen: set[str] = set()
    for fa in state.free_agents:
        key = names.person(fa.name)
        if key in claimed and key not in on_waivers:
            continue  # added by a team after showing up in the list
        if key in on_waivers:
            fa.availability = "W"
            fa.waiver_clears = clear_date(eastern(on_waivers[key].at) + waiver)
        seen.add(key)
        pool.append(fa)
    for key, t in on_waivers.items():
        if key in seen or not t.positions or not t.team or _rostered(key, state.my_team, state.opponent):
            continue
        pool.append(FreeAgentIn(name=t.player, team=t.team, positions=t.positions, availability="W",
                                waiver_clears=clear_date(eastern(t.at) + waiver)))
    if on_waivers:
        notes.append("On waivers after recent drops: " + ", ".join(
            f"{t.player} (clears {eastern(t.at) + waiver:%a %d %b %H:%M} ET)" for t in on_waivers.values()))
    state.free_agents = pool


def clear_date(clears_at: datetime) -> date:
    """First day a waiver claim can play: the clearing day, or the next one if it clears in the evening."""
    return clears_at.date() if clears_at.time() < WAIVER_SAME_DAY_CUTOFF else clears_at.date() + timedelta(days=1)


def _rostered(key: str, *teams: TeamIn) -> bool:
    return any(names.person(p.name) == key for team in teams for p in team.players)
