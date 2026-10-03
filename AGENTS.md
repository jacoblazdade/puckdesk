# puckdesk: notes for coding agents

Jacob's fantasy hockey streaming assistant for his two Yahoo head-to-head
category leagues. Python engine plus an MCP server on his Mac mini; Claude
(claude.ai) is the front end: it reads Yahoo through a connector, calls
`morning_digest` on this server, and shows the result on the Puckdesk Digest
artifact page. `README.md` says what it does; `docs/SETUP.md` is the runbook
and records the state of the Mac mini.

## The leagues

- League 1: G, A, SOG, PPP, BLK, HIT, W, SV%, GAA, SO
- League 2: G, A, SOG, PPP, BLK, HIT, PIM, FW, W, SV, SV%, GAA, SO
- Weeks start Monday. 4 to 5 adds per week. Waivers clear 03:00 ET (09:00 in
  Cologne, 08:00 during the two DST gap weeks). Sunday is prep day for next week.
- Roster tags: `core` is never suggested as a drop, `hold` only for a clear
  upgrade, `stream` freely.

## Layout

- `src/puckdesk/`: `engine.py` (matchup, moves, digest), `projection.py`
  (rates to per-day usage, goalie start probability), `simulate.py` (Monte
  Carlo), `rates.py`, `categories.py`, `models.py` (LeagueState), `store.py`
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
