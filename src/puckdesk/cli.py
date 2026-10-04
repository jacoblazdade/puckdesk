"""Command line: database setup, NHL ingest jobs, the MCP server and local runs."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import new_secret, settings
from .store import Store, dumps, previous_season, season_of


def _store() -> Store:
    return Store(settings().database_url)


def cmd_init_db(a) -> None:
    _store().init()
    print("schema ready")


def cmd_new_secret(a) -> None:
    print(new_secret())


def cmd_sync_schedule(a) -> None:
    from .nhl import NHL, sync_schedule

    season = a.season or season_of(date.today())
    print(f"{sync_schedule(NHL(), _store(), season)} games stored for {season}")


def cmd_sync_rosters(a) -> None:
    from .nhl import NHL, sync_rosters

    print(f"{sync_rosters(NHL(), _store())} roster players stored")


def cmd_sync_priors(a) -> None:
    from .nhl import NHL, sync_priors

    season = a.season or previous_season(season_of(date.today()))
    print(json.dumps(sync_priors(NHL(), _store(), season)))


def cmd_sync_games(a) -> None:
    from .nhl import NHL, sync_games

    end = date.fromisoformat(a.until) if a.until else date.today()
    start = date.fromisoformat(a.since) if a.since else end - timedelta(days=3)
    print(json.dumps(sync_games(NHL(), _store(), start, end)))


def cmd_nightly(a) -> None:
    """Schedule, rosters and the last few days of games. Safe to run several times a day."""
    from .nhl import NHL, sync_games, sync_rosters, sync_schedule

    nhl, store = NHL(), _store()
    today = date.today()
    out: dict = {}
    failed = []
    # Each step on its own: a network blip in one (the schedule, say) must not cost the box scores.
    steps = (
        ("games_scheduled", lambda: sync_schedule(nhl, store, season_of(today))),
        ("roster_players", lambda: sync_rosters(nhl, store)),
        ("games", lambda: sync_games(nhl, store, today - timedelta(days=3), today)),
    )
    for name, step in steps:
        try:
            result = step()
            out.update(result if isinstance(result, dict) else {name: result})
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            out[name] = f"error: {e}"
    print(json.dumps(out))
    if failed:
        raise SystemExit(f"nightly: {', '.join(failed)} failed")


def cmd_verify_nhl(a) -> None:
    from .nhl import NHL, verify

    print(json.dumps(verify(NHL()), indent=2, default=str))


def cmd_status(a) -> None:
    print(dumps(_store().status()))


def cmd_digest(a) -> None:
    from .engine import Engine
    from .models import LeagueState

    state = LeagueState.model_validate_json(Path(a.state).read_text())
    dg = Engine(_store(), state).digest(max_moves=a.moves)
    print(json.dumps(dg, indent=2, default=str))


def cmd_serve(a) -> None:
    import uvicorn

    from .server import app

    s = settings()
    print(f"MCP endpoint: http://{s.host}:{s.port}{s.mcp_path}")
    if s.public_url:
        print(f"Public URL for the Claude connector: {s.public_url}")
    uvicorn.run(app(s), host=s.host, port=s.port, log_level="info")


def cmd_media(a) -> None:
    """Podcasts and article feeds."""
    from . import media

    src = media.load_sources()
    store = _store()
    out: dict = {"articles": {}, "episodes": {}, "transcribed": []}
    for f in src.get("feeds", []):
        try:
            out["articles"][f["name"]] = media.sync_feed(store, f["name"], f["url"])
        except Exception as e:  # noqa: BLE001
            out["articles"][f["name"]] = f"error: {e}"
    prompts = []
    for pod in src.get("podcasts", []):
        try:
            out["episodes"][pod["name"]] = media.sync_podcast(store, pod["name"], pod["feed"], pod.get("backfill_days", 21))
        except Exception as e:  # noqa: BLE001
            out["episodes"][pod["name"]] = f"error: {e}"
        prompts.append(f"{pod['name']} fantasy hockey podcast with {', '.join(pod.get('hosts', []))}.")
    if not a.no_transcribe:
        out["transcribed"] = media.transcribe_pending(store, limit=a.limit, prompt=" ".join(prompts) + " NHL players, power play, waivers.")
    print(json.dumps(out, indent=2, default=str))


def cmd_lines(a) -> None:
    """Daily Faceoff line combinations, plus Game Day Tweets posts and goalie guesses."""
    from . import gamedaytweets, lines, media

    store = _store()
    out = {"daily_faceoff": lines.sync_all(store, a.teams or None)}
    gdt = media.load_sources().get("gamedaytweets")
    if gdt is not None:
        try:
            out["gamedaytweets"] = gamedaytweets.sync(store, pages=gdt.get("pages", 3))
        except Exception as e:  # noqa: BLE001
            out["gamedaytweets"] = f"error: {e}"
    print(json.dumps(out, indent=2, default=str))


def cmd_verify_sources(a) -> None:
    from . import lines, media

    src = media.load_sources()
    out: dict = {}
    for pod in src.get("podcasts", []):
        try:
            items = media.parse_rss(media.fetch(pod["feed"]))
            out[pod["name"]] = {"episodes": len(items), "newest": items[0].title if items else None,
                                "audio": bool(items and items[0].audio_url)}
        except Exception as e:  # noqa: BLE001
            out[pod["name"]] = f"error: {e}"
    for f in src.get("feeds", []):
        try:
            items = media.parse_rss(media.fetch(f["url"]))
            out[f["name"]] = {"items": len(items), "newest": items[0].title if items else None,
                              "content_chars": len(items[0].content or "") if items else 0}
        except Exception as e:  # noqa: BLE001
            out[f["name"]] = f"error: {e}"
    try:
        out["daily_faceoff"] = lines.verify(a.team)
    except Exception as e:  # noqa: BLE001
        out["daily_faceoff"] = f"error: {e}"
    from . import gamedaytweets

    try:
        out["gamedaytweets"] = gamedaytweets.verify()
    except Exception as e:  # noqa: BLE001
        out["gamedaytweets"] = f"error: {e}"
    print(json.dumps(out, indent=2, default=str))


def cmd_yahoo_dump(a) -> None:
    from . import yahoo

    s = settings()
    store = Store(s.database_url)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in yahoo.DUMP_PATHS:
        if "{league_key}" in p and not a.league_key:
            continue
        path = p.format(league_key=a.league_key)
        body = yahoo.get(store, s.yahoo_client_id, s.yahoo_client_secret, s.yahoo_redirect_uri, path)
        fname = out / (path.replace("/", "_").replace(";", "_").replace("=", "-") + ".json")
        fname.write_text(json.dumps(body, indent=2))
        print("saved", fname)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="puckdesk")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create or update the database schema").set_defaults(fn=cmd_init_db)
    sub.add_parser("new-secret", help="print a random secret for PUCKDESK_SECRET").set_defaults(fn=cmd_new_secret)
    s = sub.add_parser("sync-schedule", help="store the NHL schedule for a season")
    s.add_argument("--season", type=int)
    s.set_defaults(fn=cmd_sync_schedule)
    sub.add_parser("sync-rosters", help="store current NHL rosters").set_defaults(fn=cmd_sync_rosters)
    s = sub.add_parser("sync-priors", help="store last season's totals, used as priors")
    s.add_argument("--season", type=int)
    s.set_defaults(fn=cmd_sync_priors)
    s = sub.add_parser("sync-games", help="store per-game stats for a date range")
    s.add_argument("--since")
    s.add_argument("--until")
    s.set_defaults(fn=cmd_sync_games)
    sub.add_parser("nightly", help="schedule, rosters and recent games").set_defaults(fn=cmd_nightly)
    sub.add_parser("verify-nhl", help="check the NHL endpoints and field names").set_defaults(fn=cmd_verify_nhl)
    sub.add_parser("status", help="show what's in the database").set_defaults(fn=cmd_status)
    s = sub.add_parser("digest", help="run a digest from a LeagueState JSON file")
    s.add_argument("state")
    s.add_argument("--moves", type=int, default=3)
    s.set_defaults(fn=cmd_digest)
    sub.add_parser("serve", help="run the MCP server").set_defaults(fn=cmd_serve)
    s = sub.add_parser("media", help="podcasts and article feeds")
    s.add_argument("--limit", type=int, default=2, help="episodes to transcribe per run")
    s.add_argument("--no-transcribe", action="store_true")
    s.set_defaults(fn=cmd_media)
    s = sub.add_parser("lines", help="Daily Faceoff lines, Game Day Tweets posts and goalie guesses")
    s.add_argument("teams", nargs="*")
    s.set_defaults(fn=cmd_lines)
    s = sub.add_parser("verify-sources", help="check the podcast, feeds and Daily Faceoff parsing")
    s.add_argument("--team", default="TOR")
    s.set_defaults(fn=cmd_verify_sources)
    s = sub.add_parser("yahoo-dump", help="save raw Yahoo responses (after API approval)")
    s.add_argument("--league-key")
    s.add_argument("--out", default="yahoo-dump")
    s.set_defaults(fn=cmd_yahoo_dump)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
