"""A small synthetic league: two fantasy teams, a free-agent pool and a schedule."""

from datetime import date, timedelta

import pytest

from puckdesk.data import GoalieLine, MemoryData, StatLine
from puckdesk.models import FreeAgentIn, LeagueIn, LeagueState, PlayerIn, TeamIn

WEEK_START = date(2026, 10, 19)  # Monday
WEEK_END = date(2026, 10, 25)
TODAY = date(2026, 10, 22)  # Thursday


def days(*offsets):
    return [WEEK_START + timedelta(days=o) for o in offsets]


def skater_line(gp, **per_game):
    return StatLine(gp, {k: v * gp for k, v in per_game.items()})


AVG_F = dict(goals=0.25, assists=0.35, points=0.60, pp_points=0.15, shots=2.4, hits=1.2, blocks=0.6, pim=0.4, faceoff_wins=0.5)
AVG_D = dict(goals=0.08, assists=0.30, points=0.38, pp_points=0.10, shots=1.8, hits=1.5, blocks=1.8, pim=0.4)
HITTER = dict(goals=0.15, assists=0.20, points=0.35, pp_points=0.05, shots=1.8, hits=3.4, blocks=0.7, pim=0.9)
PP_QB = dict(goals=0.12, assists=0.45, points=0.57, pp_points=0.30, shots=2.2, hits=1.8, blocks=2.1, pim=0.3)


def make_data():
    d = MemoryData()
    teams = {
        # team: game days this week (0 = Monday) plus next week
        "AAA": [0, 2, 3, 5, 7, 9, 11, 13],
        "BBB": [1, 3, 4, 6, 8, 10, 12],
        "CCC": [0, 1, 3, 4, 6, 7, 9, 11, 13],
        "DDD": [2, 5, 8, 12],
        "SEA": [1, 3, 4, 6, 7, 8, 11, 13],
        "MIN": [2, 4, 5, 6, 7, 9, 11],
        "CHI": [0, 2, 3, 9, 12],
        "LAK": [1, 4, 6, 8, 10],
    }
    for t, offs in teams.items():
        d.add_games(t, days(*offs))

    pid = 100
    def add(name, team, pos, line=None, prior=None, recent=None):
        nonlocal pid
        pid += 1
        d.add_player(pid, name, team, pos)
        if line:
            d.season[pid] = line
            d.recent[pid] = recent or line
        if prior:
            d.prior[pid] = prior
        return pid

    # My skaters
    for i in range(4):
        add(f"My Center {i}", ["AAA", "BBB", "CCC", "DDD"][i], "C", skater_line(6, **AVG_F), skater_line(80, **AVG_F))
    for i in range(4):
        add(f"My Wing {i}", ["AAA", "BBB", "CCC", "DDD"][i], "L" if i % 2 else "R", skater_line(6, **AVG_F), skater_line(80, **AVG_F))
    for i in range(4):
        add(f"My Defense {i}", ["AAA", "BBB", "CCC", "DDD"][i], "D", skater_line(6, **AVG_D), skater_line(80, **AVG_D))
    add("Brody Kettering", "CHI", "L", skater_line(6, **AVG_F), skater_line(80, **AVG_F))
    # A cold core player: shots intact, points down
    add(
        "Aleksi Korhonen", "DAL", "C",
        StatLine(7, {"goals": 1, "shots": 23, "points": 2, "assists": 1}),
        StatLine(82, {"goals": 32, "shots": 235, "points": 70, "assists": 38}),
    )
    d.add_games("DAL", days(3, 5, 10, 12))
    # A hot shooter
    add("Cole Winslow", "CCC", "R", StatLine(7, {"goals": 5, "shots": 11, "points": 6}), StatLine(80, {"goals": 14, "shots": 150, "points": 35}))

    # Opponent skaters (a bit stronger in hits)
    for i in range(4):
        add(f"Opp Center {i}", ["BBB", "CCC", "AAA", "SEA"][i], "C", skater_line(6, **AVG_F), skater_line(80, **AVG_F))
    for i in range(4):
        add(f"Opp Wing {i}", ["BBB", "CCC", "SEA", "MIN"][i], "L" if i % 2 else "R", skater_line(6, **HITTER), skater_line(80, **HITTER))
    for i in range(4):
        add(f"Opp Defense {i}", ["CCC", "SEA", "MIN", "BBB"][i], "D", skater_line(6, **AVG_D), skater_line(80, **AVG_D))

    # Goalies
    def goalie(name, team, starts, team_games, sv, prior_starts=60):
        nonlocal pid
        pid += 1
        d.add_player(pid, name, team, "G")
        sa = starts * 29
        d.goalie_season[pid] = GoalieLine(starts, starts, sa * sv, sa, sa * (1 - sv), starts * 0.55, team_games)
        psa = prior_starts * 29
        d.goalie_prior[pid] = GoalieLine(prior_starts, prior_starts, psa * sv, psa, psa * (1 - sv), prior_starts * 0.5, 82)

    goalie("My Goalie A", "AAA", 5, 6, 0.912)
    goalie("My Goalie B", "BBB", 4, 6, 0.905)
    goalie("Ryan Lachance", "DDD", 2, 6, 0.890, prior_starts=25)
    goalie("Opp Goalie A", "SEA", 5, 6, 0.908)
    goalie("Opp Goalie B", "MIN", 5, 6, 0.902)

    # Free agents
    add("Rhys Tolliver", "SEA", "R", skater_line(6, **HITTER), skater_line(80, **HITTER))
    add("Luka Petrovic", "MIN", "D", skater_line(6, **PP_QB), skater_line(80, **PP_QB))
    add("Slow Plodder", "CHI", "C", skater_line(6, goals=0.05, assists=0.1, points=0.15, shots=1.0, hits=0.5), skater_line(80, goals=0.05, assists=0.1, points=0.15, shots=1.0, hits=0.5))
    goalie("Marek Hudec", "LAK", 3, 6, 0.910)
    return d


def P(name, team, pos, tag=None, status=None):
    return PlayerIn(name=name, team=team, positions=pos, tag=tag, status=status)


def make_state():
    me = [
        P("My Center 0", "AAA", ["C"], "core"), P("My Center 1", "BBB", ["C"], "core"),
        P("My Center 2", "CCC", ["C"], "core"), P("My Center 3", "DDD", ["C"], "hold"),
        P("My Wing 0", "AAA", ["RW"], "core"), P("My Wing 1", "BBB", ["LW"], "core"),
        P("My Wing 2", "CCC", ["RW"], "core"), P("My Wing 3", "DDD", ["LW"], "hold"),
        P("My Defense 0", "AAA", ["D"], "core"), P("My Defense 1", "BBB", ["D"], "core"),
        P("My Defense 2", "CCC", ["D"], "core"), P("My Defense 3", "DDD", ["D"], "stream"),
        P("Brody Kettering", "CHI", ["LW"], "stream"),
        P("Aleksi Korhonen", "DAL", ["C"], "core"),
        P("Cole Winslow", "CCC", ["RW"], "hold"),
        P("My Goalie A", "AAA", ["G"], "core"), P("My Goalie B", "BBB", ["G"], "hold"),
        P("Ryan Lachance", "DDD", ["G"], "stream"),
    ]
    opp = [
        P("Opp Center 0", "BBB", ["C"]), P("Opp Center 1", "CCC", ["C"]),
        P("Opp Center 2", "AAA", ["C"]), P("Opp Center 3", "SEA", ["C"]),
        P("Opp Wing 0", "BBB", ["RW"]), P("Opp Wing 1", "CCC", ["LW"]),
        P("Opp Wing 2", "SEA", ["RW"]), P("Opp Wing 3", "MIN", ["LW"]),
        P("Opp Defense 0", "CCC", ["D"]), P("Opp Defense 1", "SEA", ["D"]),
        P("Opp Defense 2", "MIN", ["D"]), P("Opp Defense 3", "BBB", ["D"]),
        P("Opp Goalie A", "SEA", ["G"]), P("Opp Goalie B", "MIN", ["G"]),
    ]
    fas = [
        FreeAgentIn(name="Rhys Tolliver", team="SEA", positions=["LW", "RW"], percent_rostered=11),
        FreeAgentIn(name="Luka Petrović", team="MIN", positions=["D"], percent_rostered=6),
        FreeAgentIn(name="Slow Plodder", team="CHI", positions=["C"]),
        FreeAgentIn(name="Marek Hudec", team="LAK", positions=["G"]),
    ]
    league = LeagueIn(
        name="League 1",
        categories=["G", "A", "SOG", "PPP", "BLK", "HIT", "W", "SV%", "GAA", "SO"],
        roster_slots={"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2, "BN": 4, "IR": 2},
        week_start=WEEK_START,
        week_end=WEEK_END,
        today=TODAY,
        max_weekly_adds=4,
        adds_used=1,
    )
    return LeagueState(
        league=league,
        my_team=TeamIn(name="Me", players=me, totals={"G": 7, "A": 12, "SOG": 68, "PPP": 4, "BLK": 22, "HIT": 25, "W": 2, "SV%": 0.918, "GAA": 2.41, "SO": 0, "GS": 4}),
        opponent=TeamIn(name="Puck Dynasty", players=opp, totals={"G": 5, "A": 9, "SOG": 61, "PPP": 4, "BLK": 14, "HIT": 30, "W": 2, "SV%": 0.897, "GAA": 3.02, "SO": 0, "GS": 4}),
        free_agents=fas,
    )


@pytest.fixture
def data():
    return make_data()


@pytest.fixture
def state():
    return make_state()
