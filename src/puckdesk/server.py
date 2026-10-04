"""MCP server: the engine's tools, served over streamable HTTP for Claude.

The MCP endpoint lives at /mcp/<PUCKDESK_SECRET>, so only someone with the full
URL can reach it through the public Tailscale Funnel address.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import leagues as league_config
from . import lines, media, mentions, names, yahoo
from .config import Settings
from .engine import Engine, today_eastern
from .models import FreeAgentIn, LeagueState
from .store import Store

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = """\
puckdesk is a fantasy hockey assistant for Yahoo head-to-head category leagues.
It projects the current matchup, suggests add/drop moves within the weekly add
limit, and never suggests dropping a player tagged core.

Jacob's leagues are "hockey1234123" (477.l.60199) and "The League"
(477.l.42782). Their settings, week dates, waiver rules and weekly goalie
minimums are configured on the server; call leagues to see them.

Typical flow: read the league from the user's Yahoo connector (Flaim),
assemble a LeagueState (call league_state_guide for the exact shape), then
call morning_digest. The latest digest is stored and can be re-read with
latest_digest. Roster tags live on the server: set_tags / get_tags. League
arguments take the league name or its Yahoo league key.

Each league has a strategy (win_now, balanced or rebuild) and a note, shown in
leagues and in every digest's league_info; set_strategy changes them. Follow
both when choosing and explaining moves. After a digest, read its mentions and
store a one-line takeaway per player with set_news_notes.

News: search_media searches Keeping Karlsson podcast transcripts (with
timestamps), DobberHockey articles and Game Day Tweets beat-writer posts. team_lines and lineup_changes
come from Daily Faceoff line combinations (PP units, lines, goalies, injuries).
"""


class TagIn(BaseModel):
    name: str
    tag: Literal["core", "hold", "stream"]


NEWS_NOTE_MAX_WORDS = 20


def build(settings: Settings) -> MCPServer:
    store = Store(settings.database_url)
    mcp = MCPServer(name="puckdesk", title="puckdesk fantasy hockey", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool(annotations=READ)
    def league_state_guide() -> dict:
        """How to assemble the LeagueState input from a Yahoo connector, with an example."""
        return LEAGUE_STATE_GUIDE

    @mcp.tool(annotations=WRITE)
    def morning_digest(state: LeagueState, max_moves: int = 3) -> dict:
        """Full morning digest: matchup outlook, add/drop moves within the add budget,
        cold Core players worth holding, hot shooters due to cool off, and next week's schedule.
        The result is stored and can be re-read with latest_digest."""
        _snapshot(state)
        eng = Engine(store, state)
        dg = eng.digest(max_moves=max_moves)
        try:
            dg.update(mentions.digest_news(store, eng, dg))
        except Exception as e:  # noqa: BLE001 - news is a bonus; the digest still stands
            dg["notes"] = dg.get("notes", []) + [f"News unavailable: {e}"]
        dg["news_notes"] = {}
        dg["digest_id"] = store.save_digest(eng.league.name, dg, state.model_dump(mode="json"))
        return dg

    def _snapshot(state: LeagueState) -> None:
        """Today's Yahoo-wide % rostered for everyone in the state, for the trend signal."""
        players = state.free_agents + state.my_team.players + state.opponent.players
        rows = [{"name": p.name, "team": p.team, "pct": p.percent_rostered} for p in players if p.percent_rostered is not None]
        if rows:
            try:
                store.save_rostered(today_eastern(), rows)
            except Exception:  # noqa: BLE001 - a snapshot never blocks the answer
                pass

    @mcp.tool(annotations=READ)
    def leagues() -> dict:
        """Configured leagues (settings, current week, goalie minimum) first, then any other league
        with stored digests or tags, with the time of the latest digest."""
        stored = {r["name"]: r for r in store.league_summaries()}
        today = today_eastern()
        out = []
        for cfg in league_config.load():
            week, start, end = cfg.week_of(today)
            row = stored.pop(cfg.name, {"name": cfg.name, "last_digest": None})
            st = league_config.strategy_for(cfg.name, store.strategy(cfg.name))
            out.append({**row, **cfg.summary(), "week": week, "week_start": str(start), "week_end": str(end),
                        "strategy": st["strategy"], "strategy_note": st["note"], "strategy_source": st["source"]})
        for name, row in stored.items():
            st = league_config.strategy_for(name, store.strategy(name))
            out.append({**row, "strategy": st["strategy"], "strategy_note": st["note"], "strategy_source": st["source"]})
        return {"leagues": out}

    @mcp.tool(annotations=WRITE)
    def set_strategy(league: str, strategy: Literal["win_now", "balanced", "rebuild"], note: str = "") -> dict:
        """Set a league's strategy and the note behind it. win_now: maximize this week's category wins.
        rebuild: long-term asset value first (age, role, trends, keeper ranks); this week counts little.
        balanced: in between. Overrides leagues.toml; league is the name or Yahoo league key."""
        league = league_config.canonical(league)
        store.set_strategy(league, strategy, note.strip() or None)
        st = league_config.strategy_for(league, store.strategy(league))
        return {"league": league, "strategy": st["strategy"], "strategy_note": st["note"]}

    @mcp.tool(annotations=WRITE)
    def set_news_notes(league: str, notes: dict[str, str]) -> dict:
        """Store one-line news takeaways on the league's latest digest, keyed by player name
        (at most 20 words each). latest_digest returns them as news_notes."""
        league = league_config.canonical(league)
        ok = {k.strip(): " ".join(v.split()) for k, v in notes.items() if k.strip() and v.strip()}
        too_long = {k: len(v.split()) for k, v in ok.items() if len(v.split()) > NEWS_NOTE_MAX_WORDS}
        ok = {k: v for k, v in ok.items() if k not in too_long}
        digest_id = store.set_news_notes(league, ok) if ok else None
        out: dict = {"league": league, "stored": sorted(ok) if digest_id else [], "digest_id": digest_id}
        if too_long:
            out["rejected"] = {k: f"{n} words; keep it to {NEWS_NOTE_MAX_WORDS}" for k, n in too_long.items()}
        if ok and digest_id is None:
            out["error"] = "No digest stored yet for this league."
        return out

    @mcp.tool(annotations=READ)
    def latest_digest(league: str) -> dict:
        """The most recent stored digest for a league (by league name or Yahoo league key)."""
        league = league_config.canonical(league)
        dg = store.latest_digest(league)
        if dg is None:
            return {"league": league, "error": "No digest stored yet for this league.", "known_leagues": store.leagues()}
        dg.setdefault("news_notes", {})
        return dg

    @mcp.tool(annotations=READ)
    def matchup_outlook(state: LeagueState) -> dict:
        """Projected result per category for the rest of the week, with win chances."""
        _snapshot(state)
        return Engine(store, state).matchup()

    @mcp.tool(annotations=READ)
    def plan_moves(state: LeagueState, max_moves: int = 3) -> dict:
        """Best add/drop moves for this week, scored by the league's strategy. Drops only Stream players,
        or Hold players for a clear upgrade."""
        _snapshot(state)
        return Engine(store, state).plan_moves(max_moves=max_moves)

    @mcp.tool(annotations=READ)
    def what_if(state: LeagueState, add: FreeAgentIn, drop: str) -> dict:
        """Evaluate one specific move: add a player and drop one of yours (by name)."""
        eng = Engine(store, state)
        key = names.person(drop)
        dropped = next((p for p in eng.me if p.key == key), None)
        if dropped is None:
            return {"error": f"{drop} is not on your roster."}
        base = eng.evaluate(eng.me)
        new = eng.projector.free_agent(add)
        after = eng.evaluate([p for p in eng.me if p.key != key] + [new])
        return {
            "add": add.name,
            "drop": dropped.name,
            "drop_tag": dropped.tag,
            "gain_expected_categories": round(after.expected - base.expected, 3),
            "categories": {
                b.key: {"before": round(b.expected, 3), "after": round(a.expected, 3)}
                for b, a in zip(base.results, after.results)
            },
            "games_left": {"add": new.games_left, "drop": dropped.games_left},
        }

    @mcp.tool(annotations=WRITE)
    def set_tags(league: str, tags: list[TagIn]) -> dict:
        """Tag players: core = never drop, hold = drop only for a clear upgrade, stream = drop freely.
        league is the league name or Yahoo league key."""
        league = league_config.canonical(league)
        n = store.set_tags(league, [t.model_dump() for t in tags])
        return {"league": league, "updated": n, "tags": store.tag_list(league)}

    @mcp.tool(annotations=READ)
    def get_tags(league: str) -> dict:
        """Stored roster tags for a league (by league name or Yahoo league key)."""
        league = league_config.canonical(league)
        return {"league": league, "tags": store.tag_list(league)}

    @mcp.tool(annotations=READ)
    def schedule(start: date | None = None, days: int = 7, teams: list[str] | None = None) -> dict:
        """NHL games per team over a date range, with light nights (8 or fewer games league-wide)."""
        s = start or today_eastern()
        e = s + timedelta(days=max(days, 1) - 1)
        per_date = store.games_per_date(s, e)
        light = sorted(str(d) for d, n in per_date.items() if 0 < n <= 8)
        rows = []
        wanted = {names.team(t) for t in teams} if teams else None
        for t, ds in store.all_team_dates(s, e).items():
            if wanted and t not in wanted:
                continue
            rows.append({"team": t, "games": len(ds), "light_night_games": sum(1 for d in ds if str(d) in light),
                         "dates": [str(d) for d in ds]})
        rows.sort(key=lambda r: (r["games"], r["light_night_games"]), reverse=True)
        return {"from": str(s), "to": str(e), "games_per_date": {str(d): n for d, n in per_date.items()},
                "light_nights": light, "teams": rows}

    @mcp.tool(annotations=READ)
    def player_card(name: str, team: str, goalie: bool = False) -> dict:
        """Projected per-game rates, this season, the last 14 days and last season for one player."""
        today = today_eastern()
        ref = store.find_player(name, team, goalie)
        if not ref:
            return {"error": f"No NHL player found for {name} ({team})."}
        out: dict = {"id": ref.id, "name": ref.name, "team": ref.team, "position": ref.position}
        if goalie:
            season, prior = store.goalie_lines(ref.id, today)
            from .rates import goalie_rates

            g = goalie_rates(season, prior)
            out.update({"season": season.__dict__, "last_season": prior.__dict__ if prior else None,
                        "projected": {"save_pct": round(g.sv_pct, 3), "start_share": round(g.start_share, 2),
                                      "shots_against_per_start": round(g.sa_per_start, 1)}})
        else:
            season, recent, prior = store.skater_lines(ref.id, today)
            from .rates import skater_rates

            pos = "C" if ref.position == "C" else ("D" if ref.position == "D" else "F")
            r = skater_rates(season, recent, prior, pos)
            out.update({"season": season.__dict__, "last_14_days": recent.__dict__,
                        "last_season": prior.__dict__ if prior else None,
                        "projected_per_game": {k: round(v, 3) for k, v in r.per_game.items()}})
        out["games_next_7_days"] = [str(d) for d in store.team_dates(ref.team, today, today + timedelta(days=6))]
        return out

    @mcp.tool(annotations=READ)
    def search_media(query: str, days: int = 14, limit: int = 12) -> dict:
        """Search Keeping Karlsson transcripts, DobberHockey articles and Game Day Tweets posts, newest first.
        Podcast hits carry the episode id and a timestamp (h:mm:ss); use podcast_transcript to read around it.
        Query syntax: words, "exact phrases", or, -exclude."""
        return {"query": query, "hits": media.search(store, query, days=days, limit=limit)}

    @mcp.tool(annotations=READ)
    def podcast_episodes(limit: int = 8) -> dict:
        """Recent podcast episodes and whether they're transcribed yet."""
        return {"episodes": media.episodes(store, limit)}

    @mcp.tool(annotations=READ)
    def podcast_transcript(episode_id: int, start: str = "0:00:00", minutes: float = 5) -> dict:
        """Transcript of an episode from a timestamp (h:mm:ss or seconds) for a few minutes."""
        parts = [float(p) for p in str(start).split(":")]
        sec = 0.0
        for p in parts:
            sec = sec * 60 + p
        return media.transcript(store, episode_id, sec, minutes)

    @mcp.tool(annotations=READ)
    def recent_articles(days: int = 3, limit: int = 20) -> dict:
        """Newest articles from the configured feeds (DobberHockey), with a short preview."""
        return {"articles": media.recent_articles(store, days, limit)}

    @mcp.tool(annotations=READ)
    def team_lines(team: str) -> dict:
        """Latest Daily Faceoff line combinations for a team: lines, PP units, goalies, injuries."""
        return lines.latest(store, team) or {"team": team, "error": "No line combinations stored yet."}

    @mcp.tool(annotations=READ)
    def lineup_changes(days: int = 3, teams: list[str] | None = None) -> dict:
        """Players who moved lines or PP units, goalie order changes and new injuries, from Daily Faceoff."""
        return {"changes": lines.recent_changes(store, days, teams)}

    @mcp.tool(annotations=READ)
    def data_status() -> dict:
        """How fresh the NHL data is: counts and the latest game dates ingested."""
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in store.status().items()}

    # --- plain HTTP routes ---------------------------------------------------
    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    @mcp.custom_route("/auth/yahoo/start", methods=["GET"], include_in_schema=False)
    async def yahoo_start(request: Request) -> Response:
        if request.query_params.get("key") != settings.secret:
            return PlainTextResponse("forbidden", status_code=403)
        if not (settings.yahoo_client_id and settings.yahoo_redirect_uri):
            return PlainTextResponse("Set YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET and PUCKDESK_PUBLIC_HOST first.", status_code=400)
        return RedirectResponse(yahoo.authorize_url(settings.yahoo_client_id, settings.yahoo_redirect_uri))

    @mcp.custom_route("/auth/yahoo/callback", methods=["GET"], include_in_schema=False)
    async def yahoo_callback(request: Request) -> Response:
        if not yahoo.check_state(request.query_params.get("state")):
            return PlainTextResponse("Sign-in expired or unknown; start again.", status_code=400)
        code = request.query_params.get("code")
        if not code:
            return PlainTextResponse(f"Yahoo returned no code: {request.query_params.get('error', '')}", status_code=400)
        yahoo.exchange_code(store, settings.yahoo_client_id, settings.yahoo_client_secret, settings.yahoo_redirect_uri, code)
        return JSONResponse({"yahoo": "signed in"})

    return mcp


def app(settings: Settings):
    mcp = build(settings)
    hosts = [f"127.0.0.1:{settings.port}", f"localhost:{settings.port}", "127.0.0.1", "localhost"]
    if settings.public_host:
        hosts += [settings.public_host, f"{settings.public_host}:443"]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=["https://claude.ai", "https://claude.com"] + ([f"https://{settings.public_host}"] if settings.public_host else []),
    )
    return mcp.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=settings.host,
    )


LEAGUE_STATE_GUIDE = {
    "summary": (
        "Build one LeagueState per league from Flaim: get_matchups (this week's opponent and both teams' "
        "category values), get_roster for both teams (positions, lineup slot, injury status), get_free_agents "
        "(best available with % rostered) and get_transactions (at least since Monday). Flaim doesn't return "
        "league settings, waiver status or week dates; the server has them for hockey1234123 and The League, "
        "so league needs only the name or key. Leave tags out: the server fills them from set_tags. Players "
        "without an NHL match fall back to baseline rates and are listed under notes in the digest."
    ),
    "leagues": {
        "hockey1234123": "key 477.l.60199, my team id 6 (Kapri's Papi). Goalie minimum 3 appearances.",
        "The League": "key 477.l.42782, my team id 6 (Dude Where's Makar?). Goalie minimum 2 appearances.",
    },
    "fields": {
        "league.name": "hockey1234123 or The League (the Yahoo league key works too). Nothing else needed: "
        "categories, roster, add limit, waivers, week dates and the goalie minimum come from the server's config.",
        "league.today": "Optional; defaults to today's date in US Eastern time.",
        "strategy": "Not part of the state: each league's strategy (win_now, balanced, rebuild) and note come from "
        "the server (leagues, set_strategy) and appear in the digest's league_info.",
        "my_team.totals / opponent.totals": "Every category value from get_matchups, keyed by Yahoo's display "
        "name (G, A, SOG, PPP, HIT, BLK, PIM, FW, W, GAA, SV, SV%, SHO). Also pass GA and SA, which Yahoo shows "
        "as display-only stats: the server derives goalie minutes as GA x 60 / GAA and saves as SA - GA.",
        "my_team.goalie_appearances / opponent.goalie_appearances": "Goalie appearances so far this week only if "
        "the matchup shows them (goalie GP); this overrides the server's count. Leave out otherwise: the server "
        "counts NHL games (box scores and live scores) for each goalie while he was rostered, including goalies "
        "dropped this week (from transactions), with GA, GAA and SA as a floor.",
        "opponent.team_id": "Optional: the opponent's Yahoo team id or key, so his transactions (dropped goalies) "
        "are matched; his team name works too.",
        "players[]": "From get_roster: name, team (NHL abbreviation), positions (eligible: C, LW, RW, D, G), "
        "slot (today's lineup slot: C, LW, RW, D, Util, G, BN, IR+), status (DTD, O, IR, IR-LT, NA or empty) "
        "and percent_rostered if Flaim shows it. Every % rostered passed in is stored as a daily snapshot; "
        "its trend feeds the long-term value of players.",
        "free_agents[]": "From get_free_agents: about 40 skaters across C, LW, RW and D plus 8 goalies, with "
        "percent_rostered (Yahoo-wide % rostered). Leave availability out; recent drops in transactions mark "
        "waiver players and their clearing time.",
        "transactions[]": "From get_transactions, everything since Monday 00:00 ET and at least the last 2 days: "
        "one entry per player moved, kind add or drop (an add/drop is two entries), player, team (NHL), "
        "positions, fantasy_team (team id, team key or name) and at (timestamp). Trades can be left out. My adds "
        "since Monday count as adds used; a player dropped less than 2 days ago is on waivers until drop time + 2 days.",
    },
    "example": {
        "league": {"name": "hockey1234123"},
        "my_team": {
            "name": "Kapri's Papi",
            "players": [
                {"name": "Player Name", "team": "SEA", "positions": ["LW", "RW"], "slot": "LW", "status": ""},
                {"name": "Goalie Name", "team": "TOR", "positions": ["G"], "slot": "G", "status": ""},
                {"name": "Hurt Player", "team": "CBJ", "positions": ["D"], "slot": "IR+", "status": "IR"},
            ],
            "totals": {"G": 7, "A": 12, "PPP": 4, "SOG": 68, "HIT": 25, "BLK": 22, "W": 2, "GAA": 2.41,
                       "SV%": 0.918, "SHO": 0, "GA": 5, "SA": 61},
            "goalie_appearances": 2,
        },
        "opponent": {"name": "Opponent", "players": [], "totals": {}},
        "free_agents": [{"name": "Free Agent", "team": "MIN", "positions": ["D"], "percent_rostered": 6}],
        "transactions": [
            {"kind": "add", "player": "Free Agent Two", "team": "UTA", "positions": ["C"], "fantasy_team": 6,
             "at": "2026-10-20T14:05:00-04:00"},
            {"kind": "drop", "player": "Dropped Guy", "team": "NSH", "positions": ["RW"], "fantasy_team": 3,
             "at": "2026-10-21T22:40:00-04:00"},
        ],
    },
}
