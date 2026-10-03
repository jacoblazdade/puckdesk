"""League config, completing a Flaim-built LeagueState, and the goalie minimum."""

from datetime import date, datetime

import numpy as np
import pytest

from puckdesk import leagues
from puckdesk.categories import resolve
from puckdesk.engine import Engine
from puckdesk.leaguestate import EASTERN, complete, normalize_totals
from puckdesk.models import FreeAgentIn, LeagueIn, LeagueState, PlayerIn, TeamIn, TransactionIn
from puckdesk.simulate import GoalieMinimum, TeamSim, compare

from conftest import make_data, make_state

NOW = datetime(2026, 10, 22, 6, 50, tzinfo=EASTERN)  # Thursday morning, week of 19 to 25 Oct


def et(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=EASTERN)


def test_config_has_both_leagues():
    by_name = {c.name: c for c in leagues.load()}
    h, t = by_name["hockey1234123"], by_name["The League"]
    assert (h.key, h.team_id, h.team_name) == ("477.l.60199", 6, "Kapri's Papi")
    assert (t.key, t.team_id, t.team_name, t.keeper) == ("477.l.42782", 6, "Dude Where's Makar?", True)
    assert h.categories == ["G", "A", "PPP", "SOG", "HIT", "BLK", "W", "GAA", "SV%", "SHO"]
    assert t.categories == ["G", "A", "PIM", "PPP", "SOG", "FW", "HIT", "BLK", "W", "GAA", "SV", "SV%", "SHO"]
    assert h.roster == {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 2, "G": 2, "BN": 5, "IR+": 3}
    assert t.roster == {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2, "BN": 4, "IR+": 1}
    assert (h.max_weekly_adds, h.waiver_days, h.min_goalie_appearances) == (5, 2, 3)
    assert (t.max_weekly_adds, t.waiver_days, t.min_goalie_appearances) == (5, 2, 2)
    for c in (h, t):
        for k in c.categories:
            resolve(k)  # every category is one the engine knows


def test_weeks_run_monday_to_sunday():
    cfg = leagues.find("hockey1234123")
    assert cfg.week_of(date(2026, 10, 3)) == (1, date(2026, 9, 28), date(2026, 10, 4))
    assert cfg.week_of(date(2026, 10, 4)) == (1, date(2026, 9, 28), date(2026, 10, 4))
    assert cfg.week_of(date(2026, 10, 5)) == (2, date(2026, 10, 5), date(2026, 10, 11))
    assert cfg.week_of(date(2026, 10, 22)) == (4, date(2026, 10, 19), date(2026, 10, 25))


def test_find_by_name_key_or_case():
    assert leagues.find("477.l.42782").name == "The League"
    assert leagues.find("the league").name == "The League"
    assert leagues.canonical("477.l.60199") == "hockey1234123"
    assert leagues.canonical("Some other league") == "Some other league"
    cfg = leagues.find("hockey1234123")
    assert cfg.is_my_team(6) and cfg.is_my_team("477.l.60199.t.6") and cfg.is_my_team("Kapri's Papi")
    assert not cfg.is_my_team(3) and not cfg.is_my_team("477.l.60199.t.16")


def flaim_state(**kw):
    """A LeagueState the way Claude builds it from Flaim: no settings, no week dates."""
    return LeagueState(
        league=LeagueIn(name=kw.pop("name", "477.l.60199")),
        my_team=TeamIn(name="Kapri's Papi", players=[PlayerIn(name="My Guy", team="SEA", positions=["C"])],
                       totals=kw.pop("totals", {})),
        opponent=TeamIn(name="Opp", players=[]),
        free_agents=kw.pop("free_agents", []),
        transactions=kw.pop("transactions", None),
    )


def test_complete_fills_settings_and_week_from_config():
    st, _ = complete(flaim_state(), now=NOW)
    lg = st.league
    assert (lg.name, lg.key) == ("hockey1234123", "477.l.60199")
    assert lg.roster_slots["Util"] == 2 and lg.max_weekly_adds == 5 and lg.min_goalie_appearances == 3
    assert (lg.week_start, lg.week_end) == (date(2026, 10, 19), date(2026, 10, 25))


def test_unknown_league_needs_settings():
    with pytest.raises(ValueError, match="not in leagues.toml"):
        complete(flaim_state(name="Mystery league"), now=NOW)


def test_matchup_values_from_flaim():
    t = normalize_totals({"SHO": 1, "GAA": 2.5, "GA": 5, "SA": 80, "SV%": "0.9375", "W": 2, "PIM": "-"})
    assert t["SO"] == 1 and "SHO" not in t
    assert t["MIN"] == pytest.approx(120.0)  # GA x 60 / GAA
    assert t["SV"] == 75 and t["SV%"] == pytest.approx(0.9375)
    assert "PIM" not in t  # Yahoo shows "-" before any stat
    shutout = normalize_totals({"GA": 0, "GAA": 0.0, "SA": 30})
    assert "MIN" not in shutout and shutout["SV"] == 30  # no minutes from 0 GA; the engine uses appearances


def test_transactions_give_adds_used_and_waivers():
    txs = [
        TransactionIn(kind="add", player="Old Add", fantasy_team=6, at=et(18, 23)),  # Sunday: last week
        TransactionIn(kind="add", player="Add One", fantasy_team="477.l.60199.t.6", at=et(19, 9)),
        TransactionIn(kind="drop", player="Fresh Drop", team="NSH", positions=["RW"], fantasy_team=6, at=et(19, 9)),
        TransactionIn(kind="add", player="Add Two", fantasy_team="Kapri's Papi", at=datetime(2026, 10, 21, 12)),
        TransactionIn(kind="add", player="Their Add", fantasy_team=3, at=et(21, 13)),
        TransactionIn(kind="drop", player="Recent Drop", fantasy_team=3, at=et(21, 10)),
        TransactionIn(kind="drop", player="Evening Drop", team="BOS", positions=["D"], fantasy_team=4, at=et(21, 21)),
        TransactionIn(kind="drop", player="Claimed Again", fantasy_team=4, at=et(20, 10)),
        TransactionIn(kind="add", player="Claimed Again", fantasy_team=5, at=et(22, 4)),
    ]
    fas = [FreeAgentIn(name=n, team="MIN", positions=["C"]) for n in ("Recent Drop", "Plain FA", "Claimed Again", "Fresh Drop")]
    st, notes = complete(flaim_state(free_agents=fas, transactions=txs), now=NOW)
    assert st.league.adds_used == 2  # Monday 00:00 ET onwards, my team only
    pool = {fa.name: fa for fa in st.free_agents}
    assert "Claimed Again" not in pool
    assert pool["Plain FA"].availability == "FA"
    # Dropped Wednesday 10:00 ET: on waivers until Friday 10:00, playable Friday.
    assert pool["Recent Drop"].availability == "W" and pool["Recent Drop"].waiver_clears == date(2026, 10, 23)
    # Dropped Monday 09:00: the 2 days are over, a free agent again.
    assert pool["Fresh Drop"].availability == "FA"
    # Not in Flaim's list but dropped Wednesday 21:00: added as a waiver player, clears Friday evening, plays Saturday.
    assert pool["Evening Drop"].availability == "W" and pool["Evening Drop"].waiver_clears == date(2026, 10, 24)
    assert any("Adds used" in n for n in notes) and any("Recent Drop" in n for n in notes)


def test_no_transactions_keeps_adds_used():
    st = flaim_state()
    st.league.adds_used = 3
    done, _ = complete(st, now=NOW)
    assert done.league.adds_used == 3


def _sim(wins, starts):
    n = len(wins)
    z = np.zeros(n)
    return TeamSim({"wins": np.array(wins, float), "starts": np.array(starts, float), "saves": z, "shots_against": z,
                    "goals_against": z, "minutes": z, "shutouts": z})


def test_goalie_minimum_rule():
    cats = [resolve("W")]
    # Sims: I win W on wins in all four, but my appearances (1 banked + starts) vary.
    me = _sim([3, 3, 3, 3], [2, 1, 2, 1])
    opp = _sim([1, 1, 1, 1], [3, 3, 0, 0])
    plain = compare(cats, {}, me, {}, opp)[0]
    assert plain.p_win == 1.0
    rule = compare(cats, {}, me, {}, opp, GoalieMinimum(3, banked_me=1, banked_opp=0))[0]
    # Sim 1: both meet it, I win. Sim 2: I'm short, loss. Sim 3: only the opponent is short, win. Sim 4: both short, loss.
    assert (rule.p_win, rule.p_tie, rule.p_loss) == (0.5, 0.0, 0.5)
    # Skater categories are untouched.
    sk = resolve("G")
    me.stats["goals"], opp.stats["goals"] = np.ones(4), np.zeros(4)
    assert compare([sk], {}, me, {}, opp, GoalieMinimum(3, 0, 0))[0].p_win == 1.0
    # A short opponent hands over a category I'd otherwise lose.
    me2, opp2 = _sim([0, 0], [3, 3]), _sim([5, 5], [1, 1])
    assert compare(cats, {}, me2, {}, opp2, GoalieMinimum(3, 0, 0))[0].p_win == 1.0


def short_on_goalies_state(apps=None):
    """hockey1234123 on Thursday with only Ryan Lachance (one game left) in net."""
    st = make_state()
    st.league = LeagueIn(name="hockey1234123", today=date(2026, 10, 22))
    st.my_team.players = [p for p in st.my_team.players if p.name not in ("My Goalie A", "My Goalie B")]
    st.my_team.goalie_appearances = apps
    st.opponent.goalie_appearances = 4
    st.free_agents = [fa for fa in st.free_agents if fa.name in ("Marek Hudec", "Slow Plodder")]
    return st


def test_engine_values_securing_the_goalie_minimum():
    eng = Engine(make_data(), short_on_goalies_state(apps=2), n_sims=3000, now=NOW)
    assert eng.league.name == "hockey1234123" and eng.league.max_weekly_adds == 5
    m = eng.matchup()
    gm = m["goalie_minimum"]
    assert gm["required"] == 3 and gm["me"]["so_far"] == 2 and gm["so_far_from"]["me"] == "matchup"
    assert gm["me"]["p_short"] > 0.4 and gm["opp"]["p_short"] == 0
    keys = [c["key"] for c in m["categories"]]
    assert "SHO" in keys and "SO" not in keys  # Yahoo's label
    w = next(c for c in m["categories"] if c["key"] == "W")
    assert w["p_win"] <= 1 - gm["me"]["p_short"] + 0.01

    out = eng.plan_moves(max_moves=1, screen_n=1500)
    mv = out["moves"][0]
    assert mv["add"]["name"] == "Marek Hudec"
    assert mv["goalie_minimum_risk"]["after"] < mv["goalie_minimum_risk"]["before"] - 0.2
    assert any(r.startswith("Secures the 3-appearance goalie minimum") for r in mv["reasons"])


def test_appearances_fall_back_to_box_scores():
    data = make_data()
    lach = data.find_player("Ryan Lachance", "DDD", True)
    data.appearances[lach.id] = [date(2026, 10, 19), date(2026, 10, 21), date(2026, 10, 12)]  # last one: last week
    eng = Engine(data, short_on_goalies_state(apps=None), n_sims=500, now=NOW)
    gm = eng.matchup()["goalie_minimum"]
    assert gm["me"]["so_far"] == 2 and gm["so_far_from"]["me"].startswith("NHL box scores")


def test_digest_carries_league_info():
    dg = Engine(make_data(), short_on_goalies_state(apps=2), n_sims=500, now=NOW).digest(max_moves=1)
    info = dg["league_info"]
    assert info["week"] == 4 and info["team"] == "Kapri's Papi" and info["waivers"] == "2-day continual rolling"
    assert dg["league"] == "hockey1234123"
