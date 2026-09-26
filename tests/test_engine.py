import math

from puckdesk.data import StatLine
from puckdesk.engine import Engine
from puckdesk.rates import skater_rates


def test_rates_pull_toward_prior_early_in_season():
    season = StatLine(2, {"goals": 2, "shots": 10})  # two hot games
    prior = StatLine(82, {"goals": 20, "shots": 200})
    r = skater_rates(season, season, prior, "F")
    # Goals: 1.0/game this season, 0.24 last season; two games shouldn't move it far.
    assert 0.24 < r.per_game["goals"] < 0.35
    # Shots are role-driven and move faster, but still blend.
    assert 2.4 < r.per_game["shots"] < 3.6


def test_matchup_probabilities_are_consistent(data, state):
    m = Engine(data, state, n_sims=4000).matchup()
    assert m["categories_total"] == 10
    total = 0.0
    for c in m["categories"]:
        assert math.isclose(c["p_win"] + c["p_tie"] + c["p_loss"], 1.0, abs_tol=2e-3)
        total += c["p_win"] + 0.5 * c["p_tie"]
    assert math.isclose(total, m["expected_category_wins"], abs_tol=0.02)
    # Goals: banked 7-5 with similar forwards on both sides should lean our way.
    g = next(c for c in m["categories"] if c["key"] == "G")
    assert g["p_win"] > 0.5


def test_lineup_slots_cap_games(data, state):
    eng = Engine(data, state, n_sims=1000)
    usage = eng.projector.usage(eng.me)
    for d, active in usage.by_date.items():
        skaters = [k for k in active if not any(p.key == k and p.goalie for p in eng.me)]
        assert len(skaters) <= 11  # 2C 2LW 2RW 4D 1Util
        goalies = [k for k in active if any(p.key == k and p.goalie for p in eng.me)]
        assert len(goalies) <= 2


def test_moves_never_drop_core_and_respect_budget(data, state):
    eng = Engine(data, state, n_sims=4000)
    out = eng.plan_moves(max_moves=3, screen_n=1500)
    assert out["adds"]["left"] == 3
    assert len(out["moves"]) <= 3
    core = {p.name for p in state.my_team.players if p.tag == "core"}
    for mv in out["moves"]:
        assert mv["drop"]["name"] not in core
        assert mv["gain_expected_categories"] > 0
    # The idle, low-games stream player should be the first to go if anything is.
    if out["moves"]:
        assert out["moves"][0]["drop"]["tag"] in ("stream", "hold")


def test_no_adds_left_means_no_moves(data, state):
    state.league.adds_used = 4
    out = Engine(data, state, n_sims=1000).plan_moves()
    assert out["moves"] == []
    assert out["skipped"]


def test_protected_and_regression(data, state):
    eng = Engine(data, state, n_sims=1000)
    prot = eng.protected()
    assert [p["name"] for p in prot] == ["Aleksi Korhonen"]
    assert prot[0]["expected_goals_at_normal_finishing"] > 2.5
    reg = eng.regression_watch()
    assert [p["name"] for p in reg] == ["Cole Winslow"]


def test_digest_shape(data, state):
    dg = Engine(data, state, n_sims=2000).digest()
    for key in ("matchup", "moves", "protected", "regression_watch", "next_week", "tags"):
        assert key in dg
    assert dg["next_week"]["teams"][0]["games"] >= dg["next_week"]["teams"][-1]["games"]
