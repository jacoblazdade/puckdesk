# puckdesk

A personal fantasy hockey assistant for Yahoo head-to-head category leagues.

Every morning before the waiver run it projects the current matchup category by
category, suggests add/drop moves within the weekly add limit, and explains
each one. It never suggests dropping a player you've tagged as a keeper.

This is a personal tool for my own two leagues, hockey1234123 and The League
(settings in `leagues.toml`). It reads data only; it never makes roster moves.

## What it does

- **Matchup outlook.** Banked totals plus a 10,000-run simulation of the rest
  of the week give a win chance per category, with swing categories flagged.
  Ratio categories (SV%, GAA) are built from saves, shots, goals against and
  minutes, never averaged. Yahoo's weekly goalie minimum is part of every
  simulation: a team below it can't win any goalie category, so moves that
  secure the minimum get their real value.
- **Add/drop moves.** Every free agent and droppable player pair is scored by
  the change in expected category wins, after daily lineup-slot limits.
  Moves have to beat the value of keeping an add in hand, which falls to zero
  by the end of the week.
- **Roster tags.** `core` players are never suggested as drops, `hold` players
  only for a clear upgrade, `stream` players freely.
- **Strategy per league.** `win_now` maximizes this week's category wins;
  `rebuild` scores moves mostly by long-term asset value (age, role, ice time
  and shot trends, early production, % rostered trend, Dobber's keeper
  ranks) and lists sell-high candidates and breakout free agents; `balanced`
  sits in between.
- **Context.** Core players who are cold but whose shot volume is intact
  ("hold"), players shooting far above their normal rate, next week's
  schedule with light nights, and the newest real news item for the players
  that matter that morning (at most 8), with Claude's one-line takeaways.

## How it's built

- **Engine** (`engine.py`, `projection.py`, `simulate.py`, `rates.py`): per-game
  rates blended from this season, the last 14 days and last season; lineup
  assignment per day; Monte Carlo finish to the week.
- **Data** (`nhl.py`, `store.py`): NHL schedule, rosters and per-game stats from
  the public NHL APIs into Postgres.
- **Leagues** (`leagues.toml`, `leagues.py`, `leaguestate.py`): the settings
  Flaim doesn't return (categories, roster, add limit, waivers, week dates,
  goalie minimum), plus waivers and adds used worked out from transactions.
- **MCP server** (`server.py`): the engine as tools for Claude, served over
  streamable HTTP. Claude reads the league from a Yahoo connector (Flaim),
  calls `morning_digest`, and shows the result.
- **Yahoo** (`yahoo.py`): OAuth 2.0 and read-only access, pending approval of
  Yahoo Fantasy Sports API access.

## Running it

On an always-on Mac with Homebrew:

```bash
bash deploy/macmini/setup.sh
```

This installs Postgres, loads the NHL schedule, last season's totals and
current rosters, and starts two background jobs: the MCP server and a data
refresh at 05:30, 06:45 and 12:00.

To reach it from Claude, expose the server with Tailscale Funnel and add
`https://<host>.ts.net/mcp/<PUCKDESK_SECRET>` as a custom connector.

Useful commands:

```bash
uv run puckdesk status        # what's in the database
uv run puckdesk verify-nhl    # check the NHL endpoints and field names
uv run puckdesk nightly       # refresh schedule, rosters and recent games
uv run puckdesk digest state.json   # run a digest from a LeagueState file
uv run pytest                 # tests (uses an embedded Postgres)
```

## Data sources

- NHL schedule, rosters and stats: `api-web.nhle.com` and
  `api.nhle.com/stats/rest`. Both are public but undocumented.
- Yahoo Fantasy Sports API: read-only, once access is approved.
- Keeping Karlsson podcast (RSS), transcribed locally with Whisper and
  searchable with timestamps.
- DobberHockey articles (RSS, full text).
- Daily Faceoff line combinations: lines, PP units, goalies and injuries, with
  change detection.
- Game Day Tweets: beat-writer tweets on lines, goalies and injuries, and
  starting-goalie guesses that feed the projections.

The list lives in `sources.toml`.

## The digest page

`web/` holds the Claude artifact page that shows the digest. It reads the
latest stored digest live through the puckdesk connector, lets you change
roster tags, and falls back to sample data when the connector isn't there.
Rebuild it with `python web/build.py`.

## Status

Phase 1: stats-based digest. News, podcasts and social posts come later.
Setup steps are in `docs/SETUP.md`; the scheduled Claude task that produces
the morning digest is in `docs/claude-digest-task.md`.
