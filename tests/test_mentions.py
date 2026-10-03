"""Mention matching, ranking tables, Dobber's keeper table and which players get news.

Texts are trimmed copies of real Game Day Tweets posts and the DobberHockey
"Top 300 Keeper League Skaters - October 2026" article (3 Oct 2026).
"""

from datetime import datetime, timezone

from puckdesk import keeper, mentions
from puckdesk.engine import Engine
from puckdesk.mentions import Matcher, is_table, on_top_unit

from conftest import make_data, make_state

FLAMES = ("#Flames at practice on Friday:\n\nGridin-Frost-Coronato\nHonzek-Backlund-Farabee\nZary-Strome-Sharangovich\n"
          "Tsyplakov-Pospisil-Klapka\nOthmann\n\nBahl-Whitecloud\nHanley-Nemec\nMiddleton-Parekh\nKuznetsov-Pachal\n\nWolf\nCooley")
UTAH_PP = "PP1: Cooley - Schmaltz - Keller\nGuenther - Sergachev\n\nPP2: Lee - Trocheck - Carcone\nBut - Marino\n\n#TusksUp #Blackhawks"
WILD = "Kaprizov draws a penalty. #mnwild to the power play. Shabanov is on the top unit with Kaprizov, Boldy, Eriksson Ek and Hughes."
BLUES = ("#stlblues power-play units:\n\nPP1\nSnuggerud, Thomas, Holloway, MacTavish, Fowler\n\n"
         "PP2 \nBuchnevich, Dvorsky, Neighbours, McMichael, Jiricek")
KEEPER_ARTICLE = """As always, prospects ranked within +/-5.0 ratings of each other should be considered equal.

 Oct Player Team DEF? Rating Sep Aug Change
 1 Connor McDavid EDM \xa0 438.4 1 1 0
 2 Nathan MacKinnon COL \xa0 344.0 2 2 0
 15 Cale Makar COL y 178.2 15 15 0
 141 Matvei Gridin CGY \xa0 65.2 141 140 0
 198 Easton Cowan TOR \xa0 44.2 221 221 23
 210 Max Shabanov MIN \xa0 41.7 208 208 -2
 12 Nick Suzuki MON \xa0 189.0 12 12 0

 Oct Player Team DEF? Rating Sep Aug Change
 457 Luca Cagnoni SJS y 9.1 489 487 32
 198 Easton Cowan TOR \xa0 44.2 221 221 23

 Oct Player Team DEF? Rating Sep Aug Change
 384 T.J. Hughes COL \xa0 13.6 NR NR NEW
"""

TEAMS = {"cooley": ["UTA", "CGY"], "shabanov": ["MIN"], "thomas": ["STL", "NSH"], "neighbours": ["STL"],
         "hughes": ["NJD", "NJD", "MIN", "VAN"], "power": ["BUF"]}


def test_shared_last_name_needs_the_right_team():
    m = Matcher(TEAMS)
    assert not m.matches(FLAMES, "Logan Cooley", "UTA")  # Devin Cooley's Flames post
    assert m.matches(UTAH_PP, "Logan Cooley", "UTA")  # Utah's own post
    assert not m.matches(UTAH_PP + " vs. the #Flames tonight", "Logan Cooley", "UTA")  # both teams: ambiguous
    assert m.matches("Logan Cooley skated with the Flames' fourth line in a charity game", "Logan Cooley", "UTA")
    assert not m.matches("Hughes scored twice for the Devils", "Jack Hughes", "NJD")  # brothers on one team


def test_last_name_needs_the_team():
    m = Matcher(TEAMS)
    assert m.matches(WILD, "Maxim Shabanov", "MIN")
    assert not m.matches("Shabanov had a quiet night", "Maxim Shabanov", "MIN")
    assert m.matches("Max Shabanov is on the second line", "Maxim Shabanov", "MIN") is False
    assert m.matches("Maxim Shabanov is on the second line", "Maxim Shabanov", "MIN")
    assert m.matches("Stützle and the Sens", "Tim Stutzle", "OTT")


def test_transcripts_are_lower_case_but_common_words_need_the_full_name():
    m = Matcher(TEAMS)
    assert m.matches("the wild have shabanov on the top unit now", "Maxim Shabanov", "MIN", case_sensitive=False)
    assert not m.matches("buffalo needs more from the power play", "Owen Power", "BUF", case_sensitive=False)
    assert m.matches("owen power has been great for buffalo", "Owen Power", "BUF", case_sensitive=False)


def test_top_unit_from_lineup_posts():
    m = Matcher(TEAMS)
    assert on_top_unit(WILD, m, "Maxim Shabanov", "MIN")
    assert on_top_unit(BLUES, m, "Robert Thomas", "STL")
    assert not on_top_unit(BLUES, m, "Jake Neighbours", "STL")  # PP2
    assert on_top_unit(UTAH_PP, m, "Logan Cooley", "UTA")
    assert not on_top_unit(FLAMES, m, "Logan Cooley", "UTA")


def test_ranking_tables_are_not_news():
    paras = mentions.paragraphs(KEEPER_ARTICLE, "article")
    assert [is_table(p) for p in paras] == [False, True, True, True]
    assert not is_table(FLAMES) and not is_table(WILD) and not is_table(BLUES)


def test_keeper_table():
    rows = keeper.parse(KEEPER_ARTICLE)
    by = {r["name"]: r for r in rows}
    assert len(rows) == 9  # Cowan's repeat in the risers table is skipped
    assert by["Easton Cowan"] == {"rank": 198, "name": "Easton Cowan", "team": "TOR", "defense": False,
                                  "rating": 44.2, "previous_rank": 221, "change": 23, "new": False}
    assert by["Cale Makar"]["defense"] and by["Nick Suzuki"]["team"] == "MTL"
    assert by["Max Shabanov"]["change"] == -2
    assert by["T.J. Hughes"]["new"] and by["T.J. Hughes"]["previous_rank"] is None


class FakeStore:
    """media_texts like Store's, from a list of rows."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def media_texts(self, query, days, limit=40):
        self.queries.append(query)
        words = [w.strip('"').lower() for w in query.replace(" OR ", "|").split("|")]
        return [r for r in self.rows if any(w.split()[-1] in r["text"].lower() for w in words)]


def row(kind, text, title="t", at=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), source="src"):
    return {"kind": kind, "source": source, "title": title, "at": at, "start_sec": 754.0 if kind == "podcast" else None,
            "ref": 3, "link": None, "text": text}


def test_find_skips_tables_and_returns_the_newest_real_mention():
    m = Matcher(TEAMS)
    store = FakeStore([
        row("article", KEEPER_ARTICLE + "\n\nShabanov is on the Wild's top unit and producing.", title="Top 300"),
        row("post", FLAMES),
    ])
    hit = mentions.find(store, m, "Maxim Shabanov", "MIN")
    assert hit["kind"] == "article" and hit["snippet"] == "Shabanov is on the Wild's top unit and producing."
    assert mentions.find(FakeStore([row("post", FLAMES)]), m, "Logan Cooley", "UTA") is None
    pod = mentions.find(FakeStore([row("podcast", "the wild put shabanov on the top unit")]), m, "Maxim Shabanov", "MIN")
    assert pod["timestamp"] == "0:12:34" and pod["episode_id"] == 3


def test_digest_news_picks_the_players_that_matter(monkeypatch):
    from puckdesk import lines

    monkeypatch.setattr(lines, "recent_changes", lambda store, days, teams: [])
    data, state = make_data(), make_state()
    state.my_team.players[13].status = "DTD"  # Aleksi Korhonen
    eng = Engine(data, state, n_sims=800)
    dg = eng.digest(max_moves=2)
    everyone = [p.name for p in eng.me] + [fa.name for fa in state.free_agents]
    store = FakeStore([row("post", f"{n} news: {n} skated on the top line today") for n in everyone])
    news = mentions.digest_news(store, eng, dg)
    names = list(news["mentions"])
    moves = [n for m in dg["moves"]["moves"] for n in (m["add"]["name"], m["drop"]["name"])]
    assert names[: len(moves)] == moves  # moves first
    assert "Aleksi Korhonen" in names  # injured
    assert "My Center 0" not in names  # healthy, no move, no role change: no news
    assert len(names) <= 8 and all(len(v) == 1 for v in news["mentions"].values())
