"""Game Day Tweets parsing, storage and goalie guesses in the projections.

LIVE_HOME and LIVE_GOALIES are trimmed copies of the live site (3 Oct 2026);
HOME and GOALIES cover the older embed layout the parser still accepts.
"""

import tempfile
from datetime import date

import httpx
import pytest

from puckdesk import gamedaytweets as gdt
from puckdesk import media
from puckdesk.engine import Engine
from puckdesk.store import Store

from conftest import TODAY, make_data, make_state

LIVE_HOME = """
<div class="flex flex-col">
  <blockquote class="tweet full-sized-tweet"> <p lang="" dir="ltr"><a class="handle" href="https://twitter.com/Felix_Sicard" target="_blank">@Felix_Sicard</a><br> The Hinds-Warren pairing did not see the ice in the final stretch. Just over 9 minutes for both. Patchy night for them in the defensive zone.</p> &mdash; NHL Game Day News (@GameDayNewsNHL) <a href="https://x.com/GameDayNewsNHL/status/2106249484514795610">Oct 3, 2026</a> </blockquote>
  <div class="flex-row items-center justify-center space-x-6 mx-2 w-48 sm:w-64 flex w-64 hidden sm:flex">
    <a target="_blank" class="flex items-center" href="http://www.keepingkarlsson.com">
      <img src="/assets/KK_corner_banner-bd2a8125.png" />
</a>  </div>
  <blockquote class="tweet full-sized-tweet"> <p lang="" dir="ltr"><a class="handle" href="https://twitter.com/PatrickCPresent" target="_blank">@PatrickCPresent</a><br> Fresh off signing a $43.2 million contract, Tristan Luneau logged 26:58 TOI against the defending Western Conference champs<br><br>#FlyTogether</p> &mdash; NHL Game Day Stats (@GameDayStatsNHL) <a href="https://x.com/GameDayStatsNHL/status/2106249253547085832">Oct 3, 2026</a> </blockquote>
  <blockquote class="tweet full-sized-tweet"> <p lang="" dir="ltr">VGK: Howden-Eichel-Stone<br>Connelly-Karlsson-Marner<br>Bowman-Hertl-Olofsson<br>Barbashev-Dowd-Gatcomb https://t.co/dQzxQqFWrG</p> &mdash; NHL Game Day Lines (@GameDayLines) <a href="https://x.com/GameDayLines/status/2106225942368801013">Oct 2, 2026</a> </blockquote>
  <blockquote class="tweet full-sized-tweet"> <p lang="" dir="ltr"><a class="handle" href="https://twitter.com/sammisilber" target="_blank">@sammisilber</a><br> Logan Thompson will start tomorrow vs. the Hurricanes, and Charlie Lindgren will get the call Saturday against Tampa.</p> &mdash; Game Day Goalie Starts (@GameDayLines) <a href="https://x.com/GameDayGoalies/status/2105690927922643183">Oct 1, 2026</a> </blockquote>
</div>
"""
LIVE_GOALIES = """
<div class="flex flex-row items-center justify-center space-x-8 mb-12 mt-6">
  <a class="uppercase rounded-md text-xs py-1 px-2 bg-gray-500 text-gray-100" href="/goalies?date=2026-10-02">Prev</a>
  <div class="flex flex-col text-2xl">
    Goalie Start Tweets
    <span class="text-lg ml-2 text-gray-700">
      Saturday, October 3rd
    </span>
  </div>
  <a class="uppercase rounded-md text-xs py-1 px-2 bg-gray-500 text-gray-100" href="/goalies?date=2026-10-04">Next</a>
</div>
  <h1 class="text-3xl flex items-center justify-center mt-8 space-x-4">
    <div class="flex flex-row space-x-2 w-full justify-end"><span>Carolina Hurricanes</span></div>
    <div class="text-sm text-gray-700">@</div>
    <div class="flex flex-row space-x-2 w-full"><span>Philadelphia Flyers</span></div>
  </h1>
  <div class="text-base text-gray-700 mb-8 ">7:00 pm (ET)</div>
  <div class="text-2xl flex items-start justify-center my-8 space-x-4">
    <div class="flex flex-col space-y-2 w-full justify-start items-end">
          <span class="text-lg ml-2 text-gray-700">
      Our <i>Guess</i>: <strong>Pyotr Kochetkov</strong>
        (back to back)
    </span>
    </div>
    <div class="invisible"> @ </div>
    <div class="flex flex-col space-y-2 w-full justify-start items-start">
          <span class="text-lg ml-2 text-gray-700">
      Our <i>Guess</i>: <strong>Dan Vladar</strong>
        (starter)
    </span>
    </div>
  </div>
  <h1 class="text-3xl flex items-center justify-center mt-8 space-x-4">
    <div class="flex flex-row space-x-2 w-full justify-end"><span>Washington Capitals</span></div>
    <div class="text-sm text-gray-700">@</div>
    <div class="flex flex-row space-x-2 w-full"><span>Tampa Bay Lightning</span></div>
  </h1>
  <div class="text-base text-gray-700 mb-8 ">7:00 pm (ET)</div>
  <div class="text-2xl flex items-start justify-center my-8 space-x-4">
    <div class="flex flex-col space-y-2 w-full justify-start items-end">
        <blockquote class="tweet"> <p lang="" dir="ltr"><a class="handle" href="https://twitter.com/sammisilber" target="_blank">@sammisilber</a><br> Logan Thompson will start tomorrow vs. the Hurricanes, and Charlie Lindgren will get the call Saturday against Tampa.</p> &mdash; Game Day Goalie Starts (@GameDayLines) <a href="https://x.com/GameDayGoalies/status/2105690927922643183">Oct 1, 2026</a> </blockquote>
    </div>
    <div class="invisible"> @ </div>
    <div class="flex flex-col space-y-2 w-full justify-start items-start">
          <span class="text-lg ml-2 text-gray-700">
      Our <i>Guess</i>: <strong>Andrei Vasilevskiy</strong>
        (starter)
    </span>
    </div>
  </div>
  <h1 class="text-3xl flex items-center justify-center mt-8 space-x-4">
    <div class="flex flex-row space-x-2 w-full justify-end"><span>Los Angeles Kings</span></div>
    <div class="text-sm text-gray-700">@</div>
    <div class="flex flex-row space-x-2 w-full"><span>Edmonton Oilers</span></div>
  </h1>
  <div class="text-2xl flex items-start justify-center my-8 space-x-4">
    <div class="flex flex-col space-y-2 w-full justify-start items-end">
          <span class="text-lg ml-2 text-gray-700">
      Our <i>Guess</i>: <strong>Marek Hudec</strong>
        (confirmed)
    </span>
    </div>
  </div>
"""
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


def test_parse_tweets_live_layout():
    t = gdt.parse_tweets(LIVE_HOME)
    assert [x["account"] for x in t] == ["Felix_Sicard", "PatrickCPresent", "GameDayLines", "sammisilber"]
    assert [x["relay"] for x in t] == ["GameDayNewsNHL", "GameDayStatsNHL", "GameDayLines", "GameDayGoalies"]
    assert [x["kind"] for x in t] == ["news", "stats", "lines", "goalies"]
    assert t[0]["text"].startswith("The Hinds-Warren pairing")  # handle dropped from the text
    assert "@" not in t[0]["text"] and "Game Day" not in t[0]["text"]
    assert t[2]["text"].startswith("VGK: Howden-Eichel-Stone\nConnelly-Karlsson-Marner")
    assert t[0]["id"] == "2106249484514795610"
    assert t[0]["posted_at"].date() == date(2026, 10, 3)  # decoded from the tweet id
    assert t[3]["url"] == "https://x.com/GameDayGoalies/status/2105690927922643183"


def test_live_layout_never_borrows_authors_from_earlier_posts():
    # The relay's own post comes right after a post whose handle links to
    # twitter.com/PatrickCPresent; it must stay the relay's.
    t = gdt.parse_tweets(LIVE_HOME)
    vgk = next(x for x in t if x["text"].startswith("VGK"))
    assert vgk["account"] == "GameDayLines"


def test_parse_goalies_live_layout():
    g = gdt.parse_goalies(LIVE_GOALIES)
    assert g == [
        {"name": "Pyotr Kochetkov", "status": "back to back"},
        {"name": "Dan Vladar", "status": "starter"},
        {"name": "Andrei Vasilevskiy", "status": "starter"},
        {"name": "Marek Hudec", "status": "confirmed"},
    ]  # the embedded tweet's names don't count


def test_page_date_takes_the_year_nearest_today():
    assert gdt.page_date(LIVE_GOALIES, today=date(2026, 10, 3)) == date(2026, 10, 3)
    assert gdt.page_date(LIVE_GOALIES, today=date(2027, 1, 2)) == date(2026, 10, 3)
    jan = LIVE_GOALIES.replace("Saturday, October 3rd", "Friday, January 1st")
    assert gdt.page_date(jan, today=date(2026, 12, 31)) == date(2027, 1, 1)
    assert gdt.page_date(GOALIES, today=date(2026, 10, 3)) is None


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


def test_sync_uses_the_goalie_page_date(store, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/goalies":
            return httpx.Response(200, text=LIVE_GOALIES)
        if req.url.params.get("page"):
            return httpx.Response(200, text="<p>no more</p>")
        return httpx.Response(200, text=LIVE_HOME)

    real = httpx.Client
    monkeypatch.setattr(gdt.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr("puckdesk.engine.today_eastern", lambda: date(2026, 10, 3))
    out = gdt.sync(store, pages=3)
    assert out == {"tweets_seen": 4, "tweets_new": 4, "goalie_guesses": 4, "goalie_date": date(2026, 10, 3)}
    hints = store.goalie_hints(date(2026, 10, 3), date(2026, 10, 3))
    assert {h["norm_name"]: h["status"] for h in hints}["pyotr kochetkov"] == "back to back"


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
    assert out == {"tweets_seen": 2, "tweets_new": 2, "goalie_guesses": 4, "goalie_date": date(2026, 10, 23)}
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
    # A plain starter guess beats one with a caveat (back to back, alternating).
    strong = {}
    for status in ("starter", "back to back", "alternating"):
        data.hints = [{"game_date": day, "norm_name": "ryan lachance", "team": "DDD", "status": status}]
        e = Engine(data, state, n_sims=500)
        strong[status] = e.projector.start_prob(next(p for p in e.me if p.name == "Ryan Lachance"), day)
    assert strong["starter"] == pytest.approx(0.85 * lach.avail)
    assert strong["back to back"] == strong["alternating"] == pytest.approx(0.75 * lach.avail)
    assert TODAY <= day
