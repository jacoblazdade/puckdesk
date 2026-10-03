"""The MCP server over HTTP: secret path, host checks and a real tool call."""

import json
import tempfile

import pytest
from starlette.testclient import TestClient

from puckdesk import nhl
from puckdesk.config import Settings
from puckdesk.server import app
from puckdesk.store import Store

from test_store import GOALIE, POWERPLAY, REALTIME, ROSTER, SCHEDULE, SUMMARY, FACEOFFS

pgserver = pytest.importorskip("pgserver")

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    srv = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
    store = Store(srv.get_uri())
    store.init()
    store.upsert_games(nhl.parse_schedule(SCHEDULE))
    store.upsert_players(nhl.parse_roster("SEA", ROSTER))
    merged = nhl.merge_skater_rows({"summary": SUMMARY, "realtime": REALTIME, "faceoffwins": FACEOFFS, "powerplay": POWERPLAY}, True)
    store.upsert_skater_games(nhl.skater_game_rows(merged))
    store.upsert_goalie_games(nhl.goalie_game_rows(GOALIE))
    s = Settings(database_url=srv.get_uri(), secret="s3cret", host="127.0.0.1", port=8765,
                 public_host="testserver", yahoo_client_id=None, yahoo_client_secret=None)
    with TestClient(app(s)) as c:
        yield c
    srv.cleanup()


def rpc(client, method, params=None, path="/mcp/s3cret", headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(path, json=body, headers=headers or HEADERS)


def test_health(client):
    assert client.get("/healthz").text == "ok"


def test_wrong_path_is_not_found(client):
    assert rpc(client, "tools/list", path="/mcp/wrong").status_code == 404


def test_unknown_host_rejected(client):
    r = rpc(client, "tools/list", headers={**HEADERS, "Host": "evil.example.com"})
    assert r.status_code in (400, 403, 421)


def test_tools_and_digest(client):
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})
    assert init.status_code == 200, init.text
    listed = rpc(client, "tools/list").json()
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {"morning_digest", "latest_digest", "set_tags", "schedule"} <= names

    state = {
        "league": {"name": "L", "categories": ["SOG", "HIT", "W"], "roster_slots": {"RW": 1, "G": 1},
                   "week_start": "2026-10-19", "week_end": "2026-10-25", "today": "2026-10-22"},
        "my_team": {"name": "A", "players": [{"name": "Rhys Tolliver", "team": "SEA", "positions": ["RW"]}]},
        "opponent": {"name": "B", "players": []},
    }
    r = rpc(client, "tools/call", {"name": "morning_digest", "arguments": {"state": state}}).json()
    assert "error" not in r, r
    content = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
    assert content["matchup"]["games_left"]["me"] == 2.0

    r = rpc(client, "tools/call", {"name": "latest_digest", "arguments": {"league": "L"}}).json()
    content = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
    assert content["league"] == "L"

    r = rpc(client, "tools/call", {"name": "leagues", "arguments": {}}).json()
    content = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
    by_name = {lg["name"]: lg for lg in content["leagues"]}
    assert [lg["name"] for lg in content["leagues"]][:2] == ["hockey1234123", "The League"]
    assert by_name["hockey1234123"]["key"] == "477.l.60199" and by_name["The League"]["min_goalie_appearances"] == 2
    assert by_name["L"]["last_digest"]

    # Tags accept the Yahoo league key and are stored under the league name.
    r = rpc(client, "tools/call", {"name": "set_tags", "arguments": {"league": "477.l.42782", "tags": [{"name": "Rhys Tolliver", "tag": "stream"}]}}).json()
    content = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
    assert content["league"] == "The League"

    r = rpc(client, "tools/call", {"name": "set_tags", "arguments": {"league": "L", "tags": [{"name": "Rhys Tolliver", "tag": "core"}]}}).json()
    content = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
    assert content["tags"][0]["tag"] == "core"
