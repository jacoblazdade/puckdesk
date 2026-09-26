"""Store + NHL parsing, end to end against an embedded Postgres (pgserver)."""

import tempfile
from datetime import date

import pytest

from puckdesk import nhl
from puckdesk.engine import Engine
from puckdesk.models import LeagueIn, LeagueState, PlayerIn, TeamIn
from puckdesk.store import Store

pgserver = pytest.importorskip("pgserver")


@pytest.fixture(scope="module")
def store():
    srv = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
    s = Store(srv.get_uri())
    s.init()
    yield s
    srv.cleanup()


SCHEDULE = {
    "nextStartDate": "2026-10-26",
    "gameWeek": [
        {"date": "2026-10-19", "games": [
            {"id": 2026020101, "season": 20262027, "gameType": 2, "startTimeUTC": "2026-10-19T23:00:00Z",
             "gameState": "OFF", "awayTeam": {"abbrev": "SEA"}, "homeTeam": {"abbrev": "MIN"}},
        ]},
        {"date": "2026-10-23", "games": [
            {"id": 2026020150, "season": 20262027, "gameType": 2, "startTimeUTC": "2026-10-23T23:00:00Z",
             "gameState": "FUT", "awayTeam": {"abbrev": "MIN"}, "homeTeam": {"abbrev": "SEA"}},
        ]},
        {"date": "2026-10-24", "games": [
            {"id": 2026020160, "season": 20262027, "gameType": 2, "startTimeUTC": "2026-10-24T23:00:00Z",
             "gameState": "FUT", "awayTeam": {"abbrev": "SEA"}, "homeTeam": {"abbrev": "CHI"}},
        ]},
    ],
}
ROSTER = {
    "forwards": [{"id": 8480001, "firstName": {"default": "Rhys"}, "lastName": {"default": "Tolliver"}, "positionCode": "R"}],
    "defensemen": [],
    "goalies": [{"id": 8480009, "firstName": {"default": "Kai"}, "lastName": {"default": "Moreau"}, "positionCode": "G"}],
}
SUMMARY = [{"playerId": 8480001, "gameId": 2026020101, "gameDate": "2026-10-19", "skaterFullName": "Rhys Tolliver",
            "teamAbbrev": "SEA", "positionCode": "R", "goals": 1, "assists": 0, "points": 1, "ppPoints": 1,
            "shPoints": 0, "gameWinningGoals": 0, "shots": 4, "penaltyMinutes": 2, "plusMinus": 1, "timeOnIcePerGame": 1060}]
REALTIME = [{"playerId": 8480001, "gameId": 2026020101, "gameDate": "2026-10-19", "hits": 5, "blockedShots": 1}]
FACEOFFS = [{"playerId": 8480001, "gameId": 2026020101, "gameDate": "2026-10-19", "totalFaceoffWins": 0}]
POWERPLAY = [{"playerId": 8480001, "gameId": 2026020101, "gameDate": "2026-10-19", "ppTimeOnIce": 190}]
GOALIE = [{"playerId": 8480009, "gameId": 2026020101, "gameDate": "2026-10-19", "goalieFullName": "Kai Moreau",
           "teamAbbrev": "SEA", "gamesStarted": 1, "wins": 1, "saves": 30, "shotsAgainst": 32, "goalsAgainst": 2,
           "shutouts": 0, "timeOnIce": 3600}]
PRIOR = [{"playerId": 8480001, "skaterFullName": "Rhys Tolliver", "teamAbbrevs": "CGY,SEA", "positionCode": "R",
          "gamesPlayed": 70, "goals": 12, "assists": 15, "points": 27, "ppPoints": 5, "shots": 130,
          "penaltyMinutes": 40, "plusMinus": -3, "shPoints": 0, "gameWinningGoals": 2}]


def test_parse_and_store_roundtrip(store):
    assert store.upsert_games(nhl.parse_schedule(SCHEDULE)) == 3
    assert store.upsert_players(nhl.parse_roster("SEA", ROSTER)) == 2
    merged = nhl.merge_skater_rows({"summary": SUMMARY, "realtime": REALTIME, "faceoffwins": FACEOFFS, "powerplay": POWERPLAY}, per_game=True)
    rows = nhl.skater_game_rows(merged)
    assert rows[0]["hits"] == 5 and rows[0]["pp_points"] == 1 and rows[0]["pp_toi_sec"] == 190
    store.upsert_skater_games(rows)
    store.upsert_goalie_games(nhl.goalie_game_rows(GOALIE))
    prior_merged = nhl.merge_skater_rows({"summary": PRIOR}, per_game=False)
    store.upsert_season_totals(nhl.skater_season_rows(prior_merged, 20252026))

    ref = store.find_player("Rhys Tolliver", "SEA", goalie=False)
    assert ref and ref.id == 8480001
    assert store.find_player("R. Tolliver", "SEA", goalie=False).id == 8480001
    assert store.team_dates("SEA", date(2026, 10, 19), date(2026, 10, 25)) == [
        date(2026, 10, 19), date(2026, 10, 23), date(2026, 10, 24)]
    season, recent, prior = store.skater_lines(8480001, date(2026, 10, 22))
    assert season.gp == 1 and season.sums["hits"] == 5
    assert prior.gp == 70 and prior.sums["shots"] == 130
    g, _ = store.goalie_lines(8480009, date(2026, 10, 22))
    assert g.starts == 1 and g.shots_against == 32 and g.team_games == 1


def test_engine_on_store(store):
    store.set_tags("L", [{"name": "Rhys Tolliver", "tag": "stream"}])
    league = LeagueIn(name="L", categories=["G", "SOG", "HIT", "W", "SV%"],
                      roster_slots={"RW": 1, "G": 1, "BN": 1}, week_start=date(2026, 10, 19),
                      week_end=date(2026, 10, 25), today=date(2026, 10, 22))
    st = LeagueState(
        league=league,
        my_team=TeamIn(name="A", players=[PlayerIn(name="Rhys Tolliver", team="SEA", positions=["RW"]),
                                          PlayerIn(name="Kai Moreau", team="SEA", positions=["G"])]),
        opponent=TeamIn(name="B", players=[]),
    )
    eng = Engine(store, st, n_sims=500)
    assert eng.me[0].tag == "stream"  # stored tag applied
    dg = eng.digest()
    assert dg["matchup"]["games_left"]["me"] == 2.0
    assert store.save_digest("L", dg) > 0
    assert store.latest_digest("L")["league"] == "L"
