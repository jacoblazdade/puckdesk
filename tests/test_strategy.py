"""League strategy (win_now, balanced, rebuild), asset value, sell-high and breakout
lists, and the drop fixes: IR+ slots, the IR+ note and near-tie drops."""

from datetime import date

import pytest

from puckdesk import leagues
from puckdesk.engine import Engine
from puckdesk.models import FreeAgentIn, LeagueIn
from puckdesk.value import Valuer

from conftest import AVG_F, P, make_data, make_state, skater_line

TODAY = date(2026, 10, 22)


def born(age):
    return date(TODAY.year - age, 1, 1)


def keeper_league():
    """The test league plus a young gun (one game left, top-line PP1 role) and a veteran streamer (three games)."""
    data, state = make_data(), make_state()
    data.add_player(901, "Young Gun", "DDD", "C", born(20))
    data.season[901] = data.recent[901] = skater_line(6, **AVG_F)
    data.add_player(902, "Vet Streamer", "CCC", "C", born(34))
    data.season[902] = data.recent[902] = skater_line(6, **AVG_F)
    data.prior[902] = skater_line(80, **AVG_F)
    data.roles["DDD"] = {"young gun": {"line": "f1", "pp": "pp1"}}
    state.free_agents = [
        FreeAgentIn(name="Young Gun", team="DDD", positions=["C"], percent_rostered=4),
        FreeAgentIn(name="Vet Streamer", team="CCC", positions=["C"], percent_rostered=9),
    ]
    return data, state


def run(data, state, strategy, moves=1):
    if strategy:
        data.strategies[state.league.name] = {"strategy": strategy, "note": f"{strategy} note"}
    return Engine(data, state, n_sims=2000).plan_moves(max_moves=moves, screen_n=1000)


# --- strategy -----------------------------------------------------------------------
def test_configured_strategies_and_overrides():
    by = {c.name: c for c in leagues.load()}
    assert by["hockey1234123"].strategy == "win_now" and "maximize weekly category wins" in by["hockey1234123"].strategy_note
    assert by["The League"].strategy == "rebuild" and "Shabanov" in by["The League"].strategy_note
    assert leagues.strategy_for("The League")["strategy"] == "rebuild"
    stored = {"strategy": "balanced", "note": "contending after all"}
    assert leagues.strategy_for("The League", stored) == {"strategy": "balanced", "note": "contending after all",
                                                         "source": "set_strategy"}
    assert leagues.strategy_for("Unknown league") == {"strategy": "balanced", "note": "", "source": "default"}


def test_digest_carries_the_strategy():
    data, state = keeper_league()
    data.strategies["Test league"] = {"strategy": "rebuild", "note": "rebuilding"}
    dg = Engine(data, state, n_sims=500).digest(max_moves=1)
    info = dg["league_info"]
    assert (info["strategy"], info["strategy_note"], info["strategy_source"]) == ("rebuild", "rebuilding", "set_strategy")
    assert dg["moves"]["strategy"] == "rebuild"
    assert "sell_high" in dg and "breakout_watch" in dg


def test_win_now_streams_and_rebuild_buys_upside():
    data, state = keeper_league()
    win = run(data, state, "win_now")
    assert win["moves"][0]["add"]["name"] == "Vet Streamer"
    data, state = keeper_league()
    reb = run(data, state, "rebuild")
    mv = reb["moves"][0]
    assert mv["add"]["name"] == "Young Gun"
    assert mv["asset_value"]["add"]["value"] > mv["asset_value"]["drop"]["value"] + 0.3
    assert any(r.startswith("Long-term value") and "age 20" in r for r in mv["reasons"])


def test_rebuild_never_drops_young_upside_for_a_stream():
    data, state = keeper_league()
    data.add_player(903, "Kid Prospect", "DDD", "R", born(22))
    data.season[903] = data.recent[903] = skater_line(6, goals=0.05, assists=0.05, points=0.1, shots=0.5)
    data.roles["DDD"]["kid prospect"] = {"line": "f2", "pp": None}
    for p in state.my_team.players:
        p.tag = "core"
    state.my_team.players.append(P("Kid Prospect", "DDD", ["RW"], "stream"))
    state.free_agents = [fa for fa in state.free_agents if fa.name == "Vet Streamer"]
    assert run(data, state, "win_now")["moves"][0]["drop"]["name"] == "Kid Prospect"  # win_now: games are games
    data.strategies.clear()
    assert run(data, state, "rebuild")["moves"] == []
    assert run(data, state, "balanced")["moves"] == []


def short_on_goalies(strategy):
    """Below the goalie minimum, with only a valuable veteran to drop."""
    data, state = make_data(), make_state()
    data.add_player(904, "Vet Star", "AAA", "C", born(30))
    data.season[904] = data.recent[904] = skater_line(6, **AVG_F)
    data.keeper = [{"rank": 30, "name": "Vet Star", "team": "AAA", "change": 0}]
    state.league = LeagueIn(name="hockey1234123", today=TODAY)
    state.my_team.players = [p for p in state.my_team.players if p.name not in ("My Goalie A", "My Goalie B")]
    for p in state.my_team.players:
        p.tag = "core"
    state.my_team.players.append(P("Vet Star", "AAA", ["C"], "stream"))
    state.my_team.goalie_appearances, state.opponent.goalie_appearances = 2, 4
    state.free_agents = [fa for fa in state.free_agents if fa.name == "Marek Hudec"]
    return run(data, state, strategy)


def test_rebuild_skips_goalie_streams_that_cost_value():
    assert short_on_goalies("win_now")["moves"][0]["add"]["name"] == "Marek Hudec"
    assert short_on_goalies("rebuild")["moves"] == []


# --- asset value ----------------------------------------------------------------------
def test_asset_value_signals():
    data, state = keeper_league()
    data.keeper = [{"rank": 40, "name": "Young Gun", "team": "DDD", "change": 25}]
    data.rostered["young gun"] = (18.0, 4.0, 7)
    state.free_agents[0].percent_rostered = 18  # the server stores today's value as the latest snapshot
    eng = Engine(data, state, n_sims=200)
    young = eng.asset(eng.projector.free_agent(state.free_agents[0]))
    vet = eng.asset(eng.projector.free_agent(state.free_agents[1]))
    assert young.young_upside and not vet.young_upside
    assert young.value > 1.2 and vet.value < 0.1
    for s in ("Dobber keeper rank 40", "up 25 spots in Dobber's keeper ranks", "age 20", "top-6 forward", "PP1",
              "% rostered up 14 points in 7 days"):
        assert s in young.signals
    assert young.rostered == 18 and young.rostered_change == 14


def test_keeper_rank_matches_on_last_name_and_team():
    data, state = keeper_league()
    data.keeper = [{"rank": 210, "name": "Max Gun", "team": "DDD", "change": -2}]
    eng = Engine(data, state, n_sims=200)
    assert eng.asset(eng.projector.free_agent(state.free_agents[0])).keeper_rank == 210


def test_ice_time_and_top_unit_posts():
    from puckdesk.data import IceTime

    data, state = keeper_league()
    data.roles = {}
    data.ice[902] = IceTime(gp=4, toi=19.5, pp_toi=3.0, toi_prior=15.0, pp_toi_prior=1.0)
    data.posts = [{"account": "beat", "text": "Power play at practice:\nPP1: Streamer, Someone, Another"}]
    eng = Engine(data, state, n_sims=200)
    vet = Valuer(data, TODAY, 6).asset(eng.projector.free_agent(state.free_agents[1]))
    assert "ice time up 4.5 min a game" in vet.signals and "PP time up 2.0 min a game" in vet.signals
    assert not vet.top_unit_post  # last name alone, no team in the post
    data.posts = [{"account": "beat", "text": "CCC power play at practice:\nPP1: Streamer, Someone, Another"}]
    vet = Valuer(data, TODAY, 6).asset(eng.projector.free_agent(state.free_agents[1]))
    assert vet.top_unit_post and "on the top PP unit in a recent lineup post" in vet.signals


def test_sell_high_and_breakout_watch():
    data, state = keeper_league()
    data.rostered["my center 0"] = (62.0, 41.0, 6)
    data.roles["AAA"] = {"my defense 0": {"line": "d1", "pp": "pp1"}}
    eng = Engine(data, state, n_sims=200)
    sell = {r["name"]: r["reasons"] for r in eng.sell_high()}
    assert sell["Cole Winslow"] == ["5 goals on 11 shots (45%) against a normal 9%"]
    assert sell["My Center 0"] == ["% rostered jumped 21 points to 62%"]
    assert sell["My Defense 0"][0].startswith("PP1 now, but 8 PPP in 80 games last season")
    assert "My Center 1" not in sell
    watch = eng.breakout_watch()
    assert [r["name"] for r in watch] == ["Young Gun"]
    assert watch[0]["percent_rostered"] == 4 and "PP1" in watch[0]["reasons"]


# --- the three fixes ---------------------------------------------------------------------
def full_roster_with_ir():
    data, state = make_data(), make_state()
    data.strategies["Test league"] = {"strategy": "win_now", "note": ""}
    kett = next(p for p in state.my_team.players if p.name == "Brody Kettering")
    kett.slot, kett.status = "IR+", "O"
    for p in state.my_team.players:
        if p.name not in ("Brody Kettering", "My Defense 3", "Ryan Lachance"):
            p.tag = "core"
    return data, state


def test_ir_slot_player_is_not_a_drop_when_the_roster_is_full():
    data, state = full_roster_with_ir()
    eng = Engine(data, state, n_sims=1500)
    assert eng.open_spots == 0
    out = eng.plan_moves(max_moves=2, screen_n=800)
    assert out["moves"] and all(m["drop"]["name"] != "Brody Kettering" for m in out["moves"])
    assert any("Brody Kettering" in s and "IR+" in s for s in out["skipped"])
    # With an open bench spot, dropping him is a real option again.
    state.my_team.players = [p for p in state.my_team.players if p.name != "My Center 3"]
    eng = Engine(data, state, n_sims=500)
    assert eng.open_spots == 1 and not eng._ir_blocked(next(p for p in eng.me if p.name == "Brody Kettering"))


def test_ir_note_only_for_a_healthy_player():
    data, state = make_data(), make_state()
    kett = next(p for p in state.my_team.players if p.name == "Brody Kettering")
    kett.slot, kett.status = "IR+", "DTD"
    eng = Engine(data, state, n_sims=200)
    assert not next(p for p in eng.me if p.name == "Brody Kettering").notes
    kett.status = ""
    eng = Engine(data, state, n_sims=200)
    note = next(p for p in eng.me if p.name == "Brody Kettering").notes[0]
    assert note.startswith("in the IR+ slot with no injury status: reminder to move him back")


def twins_state(old_slot="BN", young_slot="BN", young_age=22):
    """Two identical stream forwards on the same team; only age, role and slot differ."""
    data, state = make_data(), make_state()
    data.strategies["Test league"] = {"strategy": "win_now", "note": ""}
    for pid, name, age in ((911, "Old Twin", 34), (912, "Young Twin", young_age)):
        data.add_player(pid, name, "DDD", "L", born(age))
        data.season[pid] = data.recent[pid] = skater_line(6, **AVG_F)
    data.roles["DDD"] = {"young twin": {"line": "f2", "pp": None}}
    for p in state.my_team.players:
        p.tag = "core"
    state.my_team.players = [p for p in state.my_team.players if p.name != "Brody Kettering"]
    state.my_team.players += [P("Old Twin", "DDD", ["LW"], "stream"), P("Young Twin", "DDD", ["LW"], "stream")]
    state.my_team.players[-2].slot, state.my_team.players[-1].slot = old_slot, young_slot
    state.free_agents = [fa for fa in state.free_agents if fa.name == "Rhys Tolliver"]
    return data, state


def test_near_tie_drops_the_player_with_less_long_term_value():
    data, state = twins_state()
    mv = Engine(data, state, n_sims=2000).plan_moves(max_moves=1, screen_n=1000)["moves"][0]
    assert mv["drop"]["name"] == "Old Twin"


def test_near_tie_prefers_dropping_a_bench_player():
    data, state = twins_state(old_slot="LW", young_slot="BN", young_age=34)
    data.roles = {}
    mv = Engine(data, state, n_sims=2000).plan_moves(max_moves=1, screen_n=1000)["moves"][0]
    assert mv["drop"]["name"] == "Young Twin"  # same value; he's the one on the bench


@pytest.mark.parametrize("strategy,weights", [("win_now", (1.0, 0.0)), ("balanced", (0.6, 0.5)), ("rebuild", (0.2, 1.0))])
def test_weights(strategy, weights):
    data, state = make_data(), make_state()
    data.strategies["Test league"] = {"strategy": strategy, "note": ""}
    assert Engine(data, state, n_sims=100).weights == weights
