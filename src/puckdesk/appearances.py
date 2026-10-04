"""Goalie appearances a fantasy team has banked this week, for Yahoo's weekly minimum.

Counted per goalie and NHL game, once, only for games that started while the
goalie was on the fantasy team:
- goalies on the roster now and goalies dropped this week; each goalie's
  rostered windows come from the transactions (an add opens one, a drop
  closes it; no transactions means the whole week);
- ingested box scores, plus started games that aren't ingested yet or are
  still going (NHL score feed for yesterday and today, Eastern);
- a floor from the Yahoo totals: goalie minutes are at least GA x 60 / GAA
  (GAA read at its rounding edge, or MIN when passed), and no appearance
  lasts more than 65 minutes, so appearances >= ceil(minutes / 65); any
  shots, saves, goals against or wins mean at least one.
An explicit goalie_appearances in the state overrides all of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from . import names
from .data import PlayerRef
from .models import TeamIn, TransactionIn

MAX_GAME_MINUTES = 65.0  # 60 plus 5 of overtime; a shootout adds no ice time
GAA_ROUNDING = 0.005  # Yahoo shows GAA to two decimals
SLOT_NOTE = ("Yahoo only counts goalies in an active G slot; past slots aren't known, so every appearance "
             "while rostered is counted as active.")


@dataclass
class Goalie:
    name: str
    team: str
    ref: PlayerRef | None
    windows: list[tuple[datetime, datetime]]
    dropped: bool = False


@dataclass
class Banked:
    used: float
    breakdown: dict
    counted: set = field(default_factory=set)  # (player id, game id)
    note: str | None = None


def owns(fantasy_team, team: TeamIn, is_mine, cfg=None) -> bool:
    """Whether a transaction's fantasy_team is this team: id, Yahoo team key or name."""
    if fantasy_team is None:
        return False
    if is_mine and cfg is not None and cfg.is_my_team(fantasy_team, team.name):
        return True
    t = str(fantasy_team).strip()
    tid = None if team.team_id is None else str(team.team_id).strip()
    if tid and (t == tid or t.endswith(f".t.{tid.rsplit('.', 1)[-1]}") or tid.endswith(f".t.{t}")):
        return True
    return t.casefold() == team.name.strip().casefold()


def goalies_for(team: TeamIn, current: list, txs: list[TransactionIn], mine: bool, cfg, find_goalie,
                week_start: datetime, now: datetime) -> list[Goalie]:
    """Current goalies plus goalies this team dropped this week, each with rostered windows."""
    from .leaguestate import eastern

    events: dict[str, list[TransactionIn]] = {}
    for t in sorted(txs, key=lambda t: eastern(t.at)):
        if owns(t.fantasy_team, team, mine, cfg) and eastern(t.at) >= week_start:
            events.setdefault(names.person(t.player), []).append(t)

    out: list[Goalie] = []
    seen: set[str] = set()
    for p in current:  # projection.Proj
        if not p.goalie:
            continue
        seen.add(p.key)
        out.append(Goalie(p.name, p.team, p.ref, _windows(events.get(p.key, []), week_start, now, rostered=True)))
    for key, evs in events.items():
        if key in seen or not any(e.kind == "drop" for e in evs):
            continue
        last = evs[-1]
        positions = {x.upper() for x in last.positions}
        if positions and "G" not in positions:
            continue
        ref = find_goalie(last.player, last.team or "")
        if ref is None:
            continue  # not an NHL goalie
        out.append(Goalie(last.player, names.team(last.team or ref.team), ref,
                          _windows(evs, week_start, now, rostered=False), dropped=True))
    return out


def _windows(evs: list[TransactionIn], week_start: datetime, now: datetime, rostered: bool):
    from .leaguestate import eastern

    windows, start = [], None
    if not evs or evs[0].kind == "drop":
        start = week_start  # on the team since before the week began
    for e in evs:
        if e.kind == "add" and start is None:
            start = eastern(e.at)
        elif e.kind == "drop" and start is not None:
            windows.append((start, eastern(e.at)))
            start = None
    if start is not None:
        windows.append((start, now))
    return windows


def minutes_floor(totals: dict[str, float]) -> int | None:
    """Fewest appearances the Yahoo goalie totals allow, or None without goalie totals."""
    t = {k.upper(): v for k, v in totals.items() if v is not None}
    ga, gaa, sa, sv, mins, w = (t.get(k) for k in ("GA", "GAA", "SA", "SV", "MIN", "W"))
    if all(v is None for v in (ga, gaa, sa, sv, mins)):
        return None
    minutes = None
    if ga and gaa:
        minutes = ga * 60.0 / (gaa + GAA_ROUNDING)
    elif mins:
        minutes = mins
    floor = math.ceil(minutes / MAX_GAME_MINUTES - 1e-9) if minutes else 0
    if any((v or 0) > 0 for v in (sa, sv, ga, w)):
        floor = max(floor, 1)
    return floor


def banked(data, goalies: list[Goalie], live: list[dict], totals: dict, explicit: int | None,
           week_start: datetime, now: datetime, live_error: str | None = None, team_label: str = "") -> Banked:
    if explicit is not None:
        return Banked(float(explicit), {"matchup": explicit, "used": explicit})

    counted: set = set()
    box = late = from_dropped = 0
    for g in goalies:
        if g.ref is None:
            continue
        log = data.goalie_game_log(g.ref.id, week_start.date(), now.date()) if hasattr(data, "goalie_game_log") else []
        for game in log:
            if _inside(game["start"], g.windows) and (g.ref.id, game["game_id"]) not in counted:
                counted.add((g.ref.id, game["game_id"]))
                box += 1
                from_dropped += g.dropped
        for game in live:
            if game["goalies"].get(g.ref.id, 0) > 0 and _inside(game["start"], g.windows) \
                    and (g.ref.id, game["id"]) not in counted:
                counted.add((g.ref.id, game["id"]))
                late += 1
                from_dropped += g.dropped
    floor = minutes_floor(totals)
    count = box + late
    used = max(count, floor or 0)
    breakdown = {"box_scores": box, "live_or_unprocessed": late, "dropped_goalies": from_dropped,
                 "yahoo_minutes_floor": floor, "used": used, "slot_note": SLOT_NOTE}
    if live_error:
        breakdown["live_lookup"] = f"unavailable: {live_error}"
    note = None
    if floor is not None and abs(count - floor) >= 1:
        note = (f"Goalie appearances{team_label}: NHL games count {count}, the Yahoo goalie totals imply at least "
                f"{floor}; using {used}.")
    return Banked(float(used), breakdown, counted, note)


def _inside(at: datetime, windows: list[tuple[datetime, datetime]]) -> bool:
    return any(start <= at <= end for start, end in windows)
