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

from . import lines, media, names, yahoo
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

Typical flow: read the league from the user's Yahoo connector (for example
Flaim), assemble a LeagueState (call league_state_guide for the exact shape),
then call morning_digest. The latest digest is stored and can be re-read with
latest_digest. Roster tags live on the server: set_tags / get_tags.

News: search_media searches Keeping Karlsson podcast transcripts (with
timestamps), DobberHockey articles and X posts. team_lines and lineup_changes
come from Daily Faceoff line combinations (PP units, lines, goalies, injuries).
"""


class TagIn(BaseModel):
    name: str
    tag: Literal["core", "hold", "stream"]


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
        eng = Engine(store, state)
        dg = eng.digest(max_moves=max_moves)
        try:
            dg.update(_news(eng, dg))
        except Exception as e:  # noqa: BLE001 - news is a bonus; the digest still stands
            dg["notes"] = dg.get("notes", []) + [f"News unavailable: {e}"]
        dg["digest_id"] = store.save_digest(state.league.name, dg)
        return dg

    def _news(eng: Engine, dg: dict) -> dict:
        """Lineup changes and podcast/article mentions for players that matter this morning."""
        adds = [m["add"]["name"] for m in dg["moves"]["moves"]]
        mine = [p.name for p in eng.me]
        interest = {names.person(n) for n in mine + adds + [fa.name for fa in eng.state.free_agents]}
        teams = sorted({p.team for p in eng.me} | {m["add"]["team"] for m in dg["moves"]["moves"]})
        changes = [c for c in lines.recent_changes(store, days=2, teams=teams) if names.person(c["player"]) in interest]
        mentions = {}
        for n in adds + mine:
            last = n.split(" ")[-1]
            hits = media.search(store, f'"{n}" OR "{last}"', days=10, limit=3)
            if hits:
                mentions[n] = hits
        return {"lineup_changes": changes, "mentions": mentions}

    @mcp.tool(annotations=READ)
    def leagues() -> dict:
        """Leagues with stored digests or tags, newest digest time first."""
        return {"leagues": store.league_summaries()}

    @mcp.tool(annotations=READ)
    def latest_digest(league: str) -> dict:
        """The most recent stored digest for a league (by league name)."""
        dg = store.latest_digest(league)
        if dg is None:
            return {"league": league, "error": "No digest stored yet for this league.", "known_leagues": store.leagues()}
        return dg

    @mcp.tool(annotations=READ)
    def matchup_outlook(state: LeagueState) -> dict:
        """Projected result per category for the rest of the week, with win chances."""
        return Engine(store, state).matchup()

    @mcp.tool(annotations=READ)
    def plan_moves(state: LeagueState, max_moves: int = 3) -> dict:
        """Best add/drop moves for this week. Drops only Stream players, or Hold players for a clear upgrade."""
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
        """Tag players: core = never drop, hold = drop only for a clear upgrade, stream = drop freely."""
        n = store.set_tags(league, [t.model_dump() for t in tags])
        return {"league": league, "updated": n, "tags": store.tag_list(league)}

    @mcp.tool(annotations=READ)
    def get_tags(league: str) -> dict:
        """Stored roster tags for a league."""
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
        """Search Keeping Karlsson transcripts, DobberHockey articles and X posts, newest first.
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
        "Build one LeagueState per league from the Yahoo connector's league settings, both rosters in the "
        "current matchup, the scoreboard totals and the free-agent / waiver lists. Tags can be left out: the "
        "server fills them from set_tags. Players without an NHL match fall back to baseline rates and are "
        "listed under notes in the digest."
    ),
    "fields": {
        "league.name": "Stable name used for tags and stored digests, e.g. 'League 1'.",
        "league.categories": "Yahoo scoring categories, e.g. G, A, SOG, PPP, BLK, HIT, W, SV%, GAA, SO (League 2 adds PIM, FW, SV).",
        "league.roster_slots": "Counts per slot: C, LW, RW, D, Util, G, BN, IR (plus F or W if the league uses them).",
        "league.week_start / week_end": "Dates of the current scoring week (Monday to Sunday).",
        "league.today": "Optional; defaults to today's date in US Eastern time.",
        "league.max_weekly_adds / adds_used": "Weekly add limit and adds already used this week.",
        "my_team.totals / opponent.totals": "Category totals banked so far this week, keyed like the categories. "
        "Add GS (goalie starts) if known; SV, SA, GA and MIN make SV% and GAA exact.",
        "players[]": "name, team (NHL abbreviation), positions (eligible: C, LW, RW, D, G), status (DTD, O, IR...).",
        "free_agents[]": "Best available players across positions, including 5-10 goalies. availability FA or W; "
        "waiver_clears for players on waivers. 40-80 players is plenty.",
    },
    "example": {
        "league": {
            "name": "League 1",
            "categories": ["G", "A", "SOG", "PPP", "BLK", "HIT", "W", "SV%", "GAA", "SO"],
            "roster_slots": {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2, "BN": 4, "IR": 2},
            "week_start": "2026-10-19",
            "week_end": "2026-10-25",
            "max_weekly_adds": 4,
            "adds_used": 1,
        },
        "my_team": {
            "name": "My team",
            "players": [{"name": "Player Name", "team": "SEA", "positions": ["LW", "RW"], "status": ""}],
            "totals": {"G": 7, "A": 12, "SOG": 68, "PPP": 4, "BLK": 22, "HIT": 25, "W": 2, "SV%": 0.918, "GAA": 2.41, "SO": 0, "GS": 4},
        },
        "opponent": {"name": "Opponent", "players": [], "totals": {}},
        "free_agents": [{"name": "Free Agent", "team": "MIN", "positions": ["D"], "availability": "FA", "percent_rostered": 6}],
    },
}
