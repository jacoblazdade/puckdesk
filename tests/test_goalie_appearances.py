"""Goalie appearances banked this week (Yahoo's weekly minimum): dropped goalies, live and
not-yet-ingested games, the Yahoo-totals floor, and the 4 Oct 2026 digests that undercounted."""

from datetime import date, datetime

import pytest

from puckdesk import nhl
from puckdesk.appearances import minutes_floor
from puckdesk.engine import Engine
from puckdesk.leaguestate import EASTERN
from puckdesk.models import LeagueIn, TransactionIn

from conftest import P, make_data, make_state


def et(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=EASTERN)


def gm(eng):
    return eng.matchup()["goalie_minimum"]


def sunday_state(league, mine, opp=None):
    """Sunday 4 Oct 2026, 04:22 ET (the morning digest): my Yahoo totals, nothing ingested for Saturday."""
    st = make_state()
    st.league = LeagueIn(name=league, today=date(2026, 10, 4))
    st.my_team.totals = mine
    st.opponent.totals = opp or {}
    st.opponent.goalie_appearances = 3
    return st


# --- the 4 Oct digests ------------------------------------------------------------------
def test_hockey1234123_on_4_oct():
    st = sunday_state("hockey1234123", {"G": 5, "A": 19, "PPP": 8, "SOG": 75, "HIT": 53, "BLK": 26, "W": 2,
                                        "GAA": 2.25, "SV%": 0.911, "SHO": 0, "GA": 9, "SA": 101},
                      {"W": 2, "GAA": 2.83, "SV%": 0.879, "SHO": 0})
    eng = Engine(make_data(), st, n_sims=2000, now=et(10, 4, 4, 22))
    g = gm(eng)
    assert g["me"]["so_far"] >= 3 and g["me"]["p_short"] < 0.01
    assert g["so_far_from"]["me"]["yahoo_minutes_floor"] == 4 and g["so_far_from"]["me"]["used"] == 4
    cats = {c["key"]: c for c in eng.matchup()["categories"]}
    assert cats["GAA"]["p_win"] > 0.9 and cats["SV%"]["p_win"] > 0.9  # I lead both, and the minimum is met
    assert any("Yahoo goalie totals imply at least 4" in n for n in eng.prep_notes)
    moves = eng.plan_moves(max_moves=1, screen_n=500)["moves"]
    assert not any("goalie minimum" in r for m in moves for r in m["reasons"])


def test_the_league_on_4_oct():
    st = sunday_state("The League", {"W": 1, "GAA": 1.52, "SV": 41, "SV%": 0.932, "SHO": 0, "GA": 3, "SA": 44})
    eng = Engine(make_data(), st, n_sims=2000, now=et(10, 4, 4, 23))
    g = gm(eng)
    assert g["me"]["so_far"] >= 2 and g["me"]["p_short"] < 0.01
    assert g["so_far_from"]["me"]["yahoo_minutes_floor"] == 2  # about 118 minutes: more than one game


def test_minutes_floor():
    assert minutes_floor({"GA": 9, "GAA": 2.25, "SA": 101}) == 4
    assert minutes_floor({"GA": 3, "GAA": 1.52, "SA": 44}) == 2
    assert minutes_floor({"GA": 2, "GAA": 2.0, "SA": 30}) == 1  # one full game
    assert minutes_floor({"GA": 0, "GAA": 0.0, "SA": 25}) == 1  # a shutout: no minutes, but he played
    assert minutes_floor({"MIN": 185.0}) == 3
    assert minutes_floor({"W": 0, "SA": 0}) == 0
    assert minutes_floor({"G": 3, "A": 4}) is None  # no goalie totals at all


# --- who counts --------------------------------------------------------------------------
def week_state(**team):
    """The Thursday 22 Oct test week in hockey1234123 (minimum 3), my goalies' box scores in data."""
    data, st = make_data(), make_state()
    st.league = LeagueIn(name="hockey1234123", today=date(2026, 10, 22))
    st.my_team.totals = {k: v for k, v in st.my_team.totals.items() if k not in ("GS",)}
    st.opponent.goalie_appearances = 4
    for k, v in team.items():
        setattr(st.my_team, k, v)
    return data, st


def gid(data, name):
    return next(pid for pid, p in data.players.items() if p.name == name)


def test_a_goalie_dropped_mid_week_still_counts():
    data, st = week_state(totals={"W": 1, "GAA": 2.0, "SV%": 0.92})  # no GA or SA: a win only proves one
    st.my_team.players = [p for p in st.my_team.players if p.name != "My Goalie B"]
    b = gid(data, "My Goalie B")
    data.appearances[b] = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]  # Wed's game is after the drop
    data.appearances[gid(data, "My Goalie A")] = [date(2026, 10, 19)]
    st.transactions = [TransactionIn(kind="drop", player="My Goalie B", team="BBB", positions=["G"], fantasy_team=6,
                                     at=et(10, 21, 12))]
    g = gm(Engine(data, st, n_sims=500, now=et(10, 22, 6)))
    src = g["so_far_from"]["me"]
    assert (src["box_scores"], src["dropped_goalies"], src["used"]) == (3, 2, 3)
    assert g["me"]["so_far"] == 3 and src["yahoo_minutes_floor"] == 1
    assert "active G slot" in src["slot_note"]


def test_a_goalie_added_mid_week_counts_from_the_add():
    data, st = week_state()
    data.appearances[gid(data, "My Goalie A")] = [date(2026, 10, 19), date(2026, 10, 21)]
    st.my_team.players = [p for p in st.my_team.players if p.name not in ("My Goalie B", "Ryan Lachance")]
    st.my_team.totals = {}
    st.transactions = [TransactionIn(kind="add", player="My Goalie A", team="AAA", positions=["G"], fantasy_team=6,
                                     at=et(10, 20, 9))]
    assert gm(Engine(data, st, n_sims=200, now=et(10, 22, 6)))["me"]["so_far"] == 1  # Monday was before the add


def live_game(gid_, goalie_id, state, start, day, home="LAK", away="SEA", seconds=1500):
    return {"id": gid_, "date": day, "start": start, "state": state, "home": home, "away": away,
            "goalies": {goalie_id: seconds} if goalie_id else {}}


def test_a_west_coast_game_still_live_counts_once():
    data, st = week_state()
    st.my_team.totals = {}
    st.my_team.players.append(P("Marek Hudec", "LAK", ["G"]))
    hudec = gid(data, "Marek Hudec")
    data.add_games("LAK", [date(2026, 10, 22)])
    started = et(10, 22, 22)  # 7 pm Pacific
    data.live = [live_game(9001, hudec, "LIVE", started, date(2026, 10, 22))]
    data.appearances[hudec] = [(9001, started)]  # and a box score already in, mid-game
    # 01:30 ET on Friday: the game is yesterday's (Eastern) and still going.
    st.league.today = date(2026, 10, 23)
    eng = Engine(data, st, n_sims=500, now=et(10, 23, 1, 30))
    src = gm(eng)["so_far_from"]["me"]
    assert src["box_scores"] + src["live_or_unprocessed"] == 1
    # 10:30 pm ET Thursday, same game live today: banked once and out of the remaining projection.
    data.appearances[hudec] = []
    st.league.today = date(2026, 10, 22)
    eng = Engine(data, st, n_sims=500, now=et(10, 22, 22, 30))
    g = gm(eng)
    assert g["so_far_from"]["me"]["live_or_unprocessed"] == 1
    lak = next(p for p in eng.me if p.name == "Marek Hudec")
    assert date(2026, 10, 22) not in lak.dates
    usage = eng.projector.usage(eng.me)
    assert lak.key not in usage.by_date.get(date(2026, 10, 22), [])  # no start projected for tonight's game
    assert lak.key in usage.by_date[date(2026, 10, 23)]  # his later games still are


def test_a_finished_game_not_ingested_yet_counts():
    data, st = week_state()
    st.my_team.totals = {}
    a = gid(data, "My Goalie A")
    data.live = [live_game(9002, a, "OFF", et(10, 21, 19), date(2026, 10, 21), home="AAA", away="BBB"),
                 live_game(9003, None, "FUT", et(10, 22, 19), date(2026, 10, 22))]
    src = gm(Engine(data, st, n_sims=200, now=et(10, 22, 2)))["so_far_from"]["me"]
    assert (src["box_scores"], src["live_or_unprocessed"], src["used"]) == (0, 1, 1)


def test_explicit_appearances_override_everything():
    data, st = week_state(goalie_appearances=1)
    st.my_team.totals = {"GA": 9, "GAA": 2.25, "SA": 101}
    data.appearances[gid(data, "My Goalie A")] = [date(2026, 10, 19), date(2026, 10, 20)]
    g = gm(Engine(data, st, n_sims=200, now=et(10, 22, 6)))
    assert g["me"]["so_far"] == 1 and g["so_far_from"]["me"] == {"matchup": 1, "used": 1}


def test_box_scores_alone_without_yahoo_goalie_totals():
    data, st = week_state()
    st.my_team.totals = {"G": 7, "A": 12}
    data.appearances[gid(data, "My Goalie A")] = [date(2026, 10, 19), date(2026, 10, 21)]
    eng = Engine(data, st, n_sims=200, now=et(10, 22, 6))
    src = gm(eng)["so_far_from"]["me"]
    assert (src["box_scores"], src["yahoo_minutes_floor"], src["used"]) == (2, None, 2)
    assert not any("Goalie appearances" in n for n in eng.prep_notes)


def test_live_lookup_failure_is_a_note_not_an_error():
    data, st = week_state()

    def broken(dates):
        raise OSError("nodename nor servname provided")

    data.live_games = broken
    eng = Engine(data, st, n_sims=200, now=et(10, 22, 6))
    assert any(n.startswith("Live NHL scores unavailable") for n in eng.prep_notes)
    assert gm(eng)["so_far_from"]["me"]["live_lookup"].startswith("unavailable")


# --- NHL live feed parsing (trimmed copies of the live endpoints, 4 Oct 2026) ---------------
BOXSCORE = {"id": 2026020031, "gameState": "OFF", "playerByGameStats": {
    "awayTeam": {"goalies": [{"playerId": 8480280, "name": {"default": "J. Swayman"}, "toi": "00:00", "starter": False},
                             {"playerId": 8480022, "name": {"default": "M. DiPietro"}, "toi": "57:53", "starter": True}]},
    "homeTeam": {"goalies": [{"playerId": 8482661, "name": {"default": "J. Wallstedt"}, "toi": "60:00", "starter": True},
                             {"playerId": 8475717, "name": {"default": "C. Pickard"}, "toi": "00:00", "starter": False}]}}}
SCORES = {"games": [
    {"id": 2026020031, "gameState": "OFF", "startTimeUTC": "2026-10-04T00:00:00Z", "gameDate": "2026-10-03",
     "awayTeam": {"abbrev": "BOS"}, "homeTeam": {"abbrev": "MIN"}},
    {"id": 2026020034, "gameState": "LIVE", "startTimeUTC": "2026-10-04T02:30:00Z", "gameDate": "2026-10-03",
     "awayTeam": {"abbrev": "LAK"}, "homeTeam": {"abbrev": "SJS"}},
    {"id": 2026020040, "gameState": "FUT", "startTimeUTC": "2026-10-04T23:00:00Z", "gameDate": "2026-10-03",
     "awayTeam": {"abbrev": "TOR"}, "homeTeam": {"abbrev": "MTL"}},
]}


class FakeNHL:
    def __init__(self):
        self.boxscores = []

    def scores(self, day):
        return SCORES

    def boxscore(self, game_id):
        self.boxscores.append(game_id)
        return BOXSCORE


def test_boxscore_goalies_and_live_games():
    assert nhl.parse_boxscore_goalies(BOXSCORE) == {8480022: 3473, 8482661: 3600}
    fake = FakeNHL()
    games = nhl.live_games(fake, date(2026, 10, 3), ingested={2026020034})
    assert [(g["id"], g["state"]) for g in games] == [(2026020031, "OFF"), (2026020034, "LIVE")]
    assert fake.boxscores == [2026020031]  # ingested games and games not started need no box score
    assert games[0]["start"] == datetime.fromisoformat("2026-10-04T00:00:00+00:00")
    assert games[1]["goalies"] == {} and games[1]["ingested"]


@pytest.mark.parametrize("toi,sec", [("57:53", 3473), ("00:00", 0), (None, 0), ("65:00", 3900)])
def test_toi_seconds(toi, sec):
    assert nhl.toi_seconds(toi) == sec
