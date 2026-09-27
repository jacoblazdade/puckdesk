"""Game Day Tweets parsing, storage and goalie guesses in the projections."""

import tempfile
from datetime import date

import httpx
import pytest

from puckdesk import gamedaytweets as gdt
from puckdesk import media
from puckdesk.engine import Engine
from puckdesk.store import Store

from conftest import TODAY, make_data, make_state

HOME = """
<div class="tweet"><a href="https://twitter.com/jkamckenzie">@jkamckenzie</a>
<blockquote class="twitter-tweet" data-dnt="true"><p lang="en" dir="ltr">Eklund-Stützle-Zetterlund / Rhys Tolliver moves up to PP1 today</p>
&mdash; NHL Game Day Lines (@GameDayLines) <a href="https://twitter.com/GameDayLines/status/1971654321098765312?ref_src=twsrc">September 26, 2026</a></blockquote></div>
<div class="tweet"><a href="https://twitter.com/PenguinsPR">@PenguinsPR</a>
<blockquote class="twitter-tweet"><p lang="en">Forward Bryan Rust will not return to this afternoon&#39;s game.</p>
&mdash; NHL Game Day News (@GameDayNewsNHL) <a href="https://twitter.com/GameDayNewsNHL/status/1971650000000000000">September 26, 2026</a></blockquote></div>
"""
GOALIES = """<h2>Our Guess</h2><div>Jacob Markstrom (starter)</div><div>Brandon Bussi (starter)</div>
<div>Marek Hudec (confirmed)</div><div>Joey Backup (unconfirmed)</div>"""


def test_parse_tweets():
    t = gdt.parse_tweets(HOME)
    assert [x["account"] for x in t] == ["jkamckenzie", "PenguinsPR"]
    assert t[0]["kind"] == "lines" and t[1]["kind"] == "news"
    assert "Tolliver moves up to PP1" in t[0]["text"]
    assert t[1]["text"].endswith("afternoon's game.")
    assert t[0]["posted_at"].year >= 2025  # decoded from the tweet id
    assert t[0]["url"].startswith("https://x.com/GameDayLines/status/")


def test_parse_goalies():
    g = gdt.parse_goalies(GOALIES)
    assert {"name": "Marek Hudec", "status": "confirmed"} in g
    assert len(g) == 4


@pytest.fixture(scope="module")
def store():
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
    s = Store(srv.get_uri())
    s.init()
    s.upsert_players([{"id": 9001, "name": "Marek Hudec", "team": "LAK", "position": "G"}])
    yield s
    srv.cleanup()


def test_sync_stores_posts_and_guesses(store, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/goalies":
            return httpx.Response(200, text=GOALIES)
        if req.url.params.get("page"):
            return httpx.Response(200, text="<p>no more</p>")
        return httpx.Response(200, text=HOME)

    real = httpx.Client
    monkeypatch.setattr(gdt.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = gdt.sync(store, pages=3, game_date=date(2026, 10, 23))
    assert out == {"tweets_seen": 2, "tweets_new": 2, "goalie_guesses": 4}
    hints = store.goalie_hints(date(2026, 10, 23), date(2026, 10, 23))
    hudec = next(h for h in hints if h["norm_name"] == "marek hudec")
    assert hudec["team"] == "LAK" and hudec["status"] == "confirmed"
    hits = media.search(store, "Tolliver", days=3650)
    assert hits and hits[0]["kind"] == "post" and hits[0]["source"] == "jkamckenzie"


def test_goalie_guess_changes_start_probability():
    data, state = make_data(), make_state()
    base = Engine(data, state, n_sims=500)
    lach = next(p for p in base.me if p.name == "Ryan Lachance")
    day = lach.dates[0]
    before = base.projector.start_prob(lach, day)
    data.hints = [{"game_date": day, "norm_name": "ryan lachance", "team": "DDD", "status": "confirmed"}]
    after = Engine(data, state, n_sims=500)
    lach2 = next(p for p in after.me if p.name == "Ryan Lachance")
    assert before < 0.5 and after.projector.start_prob(lach2, day) > 0.95
    # Someone else guessed in net for his team: his chance drops.
    data.hints = [{"game_date": day, "norm_name": "other goalie", "team": "DDD", "status": "starter"}]
    third = Engine(data, state, n_sims=500)
    lach3 = next(p for p in third.me if p.name == "Ryan Lachance")
    assert third.projector.start_prob(lach3, day) <= 0.12
    assert TODAY <= day
