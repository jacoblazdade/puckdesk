# puckdesk: notes for coding agents

Jacob's fantasy hockey streaming assistant for his two Yahoo head-to-head
category leagues. Python engine plus an MCP server on his Mac mini; Claude
(claude.ai) is the front end: it reads Yahoo through a connector, calls
`morning_digest` on this server, and shows the result on the Puckdesk Digest
artifact page. `README.md` says what it does; `docs/SETUP.md` is the runbook
and records the state of the Mac mini.

## The leagues

Configured in `leagues.toml` (the source of truth; `leagues.py` loads it).
Use these names everywhere: tags and digests are stored under them, and tools
also accept the Yahoo league key.

- **hockey1234123** (477.l.60199), team 6 "Kapri's Papi", 10 teams: G, A,
  PPP, SOG, HIT, BLK, W, GAA, SV%, SHO. Roster C, C, LW, LW, RW, RW, D x4,
  Util x2, G x2, 5 BN, 3 IR+. Minimum 3 goalie appearances. Strategy
  `win_now`.
- **The League** (477.l.42782), team 6 "Dude Where's Makar?", 20-team keeper
  league: G, A, PIM, PPP, SOG, FW, HIT, BLK, W, GAA, SV, SV%, SHO. Roster C,
  C, LW, LW, RW, RW, D x4, Util, G x2, 4 BN, 1 IR+. Minimum 2 goalie
  appearances. Strategy `rebuild`.
- Both: 5 adds per week, 2-day continual rolling waivers (a dropped player is
  on waivers until drop time + 2 days). Weeks run Monday to Sunday; week 1
  ends Sunday 4 Oct 2026. Sunday is prep day for next week.
- Goalie minimum (Yahoo rule): a team below it can't win any goalie category
  that week; an appearance means the goalie touched the ice. `simulate.compare`
  applies it per simulation (if both teams are short, it counts as a loss).
- Roster tags: `core` is never suggested as a drop, `hold` only for a clear
  upgrade, `stream` freely.
- IR+ takes DTD, O, IR and IR-LT. A player in an IR+ slot is never a drop
  while the roster is full (dropping him frees no roster spot for the add).

## Strategy and asset value

Each league has a strategy and a note (`leagues.toml`; `set_strategy`
overrides are stored in `league_strategy`; unconfigured leagues are
`balanced`). Moves are scored as week weight x change in expected category
wins + asset weight x change in asset value: win_now 1.0/0, balanced 0.6/0.5,
rebuild 0.2/1.0 (`engine.WEIGHTS`).

`value.py` gives every player an asset value (about 1.0 = top-60 keeper
asset, under 0.2 = replaceable) from: Dobber's Top 300 Keeper League rank and
change (`keeper.py` parses the monthly article), age (birth dates from the
NHL roster API, or the player page for injured players), Daily Faceoff line
and PP unit, Game Day Tweets posts putting him on the top unit, ice time and
shot trends against last season, early production, and Yahoo-wide %
rostered with its trend (daily snapshots in `rostered_snapshots`, written from
every LeagueState). Each bonus carries a short reason.

- rebuild and balanced never drop a young upside player (25 or younger with a
  top-6 / top-4 D or PP1 role, or rising % rostered) for a lower-value add;
  rebuild only streams goalies when the drop has little value (under 0.2).
- In every strategy, a move must leave the roster able to fill as many active
  slots as before (`Engine.fillable`: a matching that counts every position a
  player is eligible at; IR-slot players don't count). Among drops for the
  same add, one at the add's position wins when it scores within 0.10 of the
  best.
- In every strategy, drops within 0.05 of each other count as a tie: the one
  with less asset value goes, and a bench player before a regular.
- Digest sections `sell_high` (my players at a likely peak: hot shooting,
  % rostered jump, PP1 he didn't have last season) and `breakout_watch`
  (free agents with a rising role and under 30% rostered).

## News in the digest

`mentions.py`: a text mentions a player with his full name, or his last name
plus his team (city, nickname, hashtag or abbreviation). A shared last name
doesn't count when the other player's team is in the text or both play for
the same team. Ranking-table paragraphs are skipped. Only players in the
moves, sell_high, breakout_watch, role changes and my injured players get a
mention: the newest real item each, at most 8. Claude stores one-line
takeaways (20 words max) with `set_news_notes`; `latest_digest` returns them
as `news_notes`.

## Flaim

Flaim (the Yahoo connector in claude.ai) is the Yahoo source until Yahoo
approves API access. It doesn't return league settings, waiver status or week
dates. Claude copies what it does return into a LeagueState
(`league_state_guide` documents the shape) and `leaguestate.complete()` fills
in the rest before the engine runs:

- settings and the current week from `leagues.toml`;
- matchup values keyed by Yahoo names (SHO becomes SO internally, but output
  keeps the league's labels); GA and SA arrive as display-only stats, goalie
  minutes are GA x 60 / GAA when GA > 0, saves SA - GA;
- positions, lineup slot (`slot`) and injury status from get_roster;
  % rostered from get_free_agents;
- from get_transactions: players dropped within the waiver period are on
  waivers until drop time + 2 days; my adds since Monday 00:00 ET are the
  adds used.

Goalie appearances so far come from the matchup when Claude passes them,
otherwise from NHL box scores for the team's current goalies.

## Layout

- `leagues.toml`: Jacob's leagues (see above).
- `src/puckdesk/`: `engine.py` (matchup, moves, digest), `projection.py`
  (rates to per-day usage, goalie start probability), `simulate.py` (Monte
  Carlo, goalie minimum), `rates.py`, `categories.py`, `models.py`
  (LeagueState), `leagues.py` (league config, weeks, strategy),
  `leaguestate.py` (completes a Flaim-built LeagueState), `value.py` (asset
  value), `keeper.py` (Dobber keeper table), `mentions.py` (news matching),
  `store.py`
  (Postgres), `nhl.py` (public NHL APIs), `media.py` (podcast RSS, Whisper,
  DobberHockey, full-text search), `lines.py` (Daily Faceoff lines),
  `gamedaytweets.py` (beat-writer tweets and goalie guesses), `yahoo.py`
  (OAuth, waiting for Yahoo API approval), `server.py` (MCP tools), `cli.py`.
- `src/puckdesk/schema.sql` is applied by `puckdesk init-db` (idempotent);
  keep every change to it idempotent.
- `sources.toml`: news source settings.
- `deploy/macmini/`: `setup.sh` (safe to re-run) and four launchd agents
  (`com.puckdesk.server`, `.nightly` 05:30/06:45/12:00,
  `.lines` 06:20/11:30/16:20/21:30, `.media` every 2 h).
  Logs: `~/Library/Logs/puckdesk-<job>.log`.
- `web/`: the digest artifact page source. `docs/claude-digest-task.md`: the
  prompt for the daily scheduled Claude task.

## Commands

    uv run pytest -q                 # tests; Postgres comes from pgserver, no setup needed
    uv run puckdesk status           # what's in the database
    uv run puckdesk verify-sources   # live check of every news source parser
    uv run puckdesk lines            # Daily Faceoff + Game Day Tweets now
    uv run puckdesk media --limit 1  # podcast/article sync and one transcription
    launchctl kickstart -k gui/$(id -u)/com.puckdesk.server   # restart the server after code changes

The jobs run `uv run --project ~/puckdesk`, so code changes apply on the next
run; only the server needs a restart. The repo must stay outside
`~/Documents`, `~/Desktop` and `~/Downloads` (macOS blocks launchd jobs there).

## Sources

- **Daily Faceoff**: team line combinations come from `__NEXT_DATA__`
  (`.props.pageProps.combinations.players`). The starting-goalies page is
  client-rendered and not parsed. Utah's slug is `utah-mammoth`.
- **Game Day Tweets** (layout checked 3 Oct 2026), server-rendered:
  - Home page (`/`, then `/?page=2` and so on), 50 posts per page:

        <blockquote class="tweet full-sized-tweet">
          <p><a class="handle" href="https://twitter.com/Felix_Sicard" target="_blank">@Felix_Sicard</a><br> The Hinds-Warren pairing ...</p>
          &mdash; NHL Game Day News (@GameDayNewsNHL) <a href="https://x.com/GameDayNewsNHL/status/2106249484514795610">Oct 3, 2026</a>
        </blockquote>

    The account is `a.handle`; without one it's the relay's own post. Relay
    and id come from the `x.com/<relay>/status/<id>` link, the post time from
    the id (snowflake). Relays: GameDayLines, GameDayNewsNHL, GameDayStatsNHL,
    GameDayGoalies (`RELAY_KIND` maps them to kinds). Ad blocks sit between
    posts. The older `twitter-tweet` embed (beat writer linked just before
    it) is still accepted; only that layout looks back for authors.
  - Goalie page (`/goalies`): a date heading like "Saturday, October 3rd"
    (`page_date()` picks the year nearest today), then per game
    "Our <i>Guess</i>: <strong>Name</strong> (reason)" for each team that has
    one. Reasons seen: starter, back to back, alternating. Embedded tweets on
    that page also contain names and brackets, so only "Our Guess:" lines count.
  - `projection.start_prob`: confirmed 0.97; starter, likely, probable,
    expected, projected 0.85 (`STRONG_GUESS`); any other reason 0.75.
- **Keeping Karlsson** (podcast RSS, transcribed locally with mlx-whisper
  large-v3-turbo) and **DobberHockey** (article RSS).
- Left Wing Lock and Frozen Tools are paywalled: don't scrape them. No
  X/Twitter API.

## Conventions

- Jacob prefers no em dashes in anything written for him (docs, code
  comments, commit messages, UI text, replies).
- Read-only toward Yahoo: puckdesk never makes roster moves.
- Keep `uv run pytest -q` green; add a test with every parser change, using a
  trimmed copy of the real HTML.
- Commit with clear messages. Never commit `.env` or secrets; the GitHub repo
  is public.
- Ask Jacob before anything that needs sudo, deletes data, resets the
  Tailscale serve/Funnel config, or changes Tailscale or GitHub settings. He
  does passwords and sign-ins himself.
- Don't rename the Tailscale machine or tailnet: the connector URL depends on
  them.
