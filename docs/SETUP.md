# Setup at home

About 45 minutes, most of it waiting for installs. Steps 3 to 5 can also be
done by Claude once the Mac mini is linked to the chat (step 2).

Already done: the Yahoo API application is in review (1 to 2 weeks), the code
is in this repo, and the **Puckdesk Digest** page exists in Claude (it shows
sample data until the connector is added).

## State of the Mac mini (3 Oct 2026)

Done:

- Homebrew, postgresql@17, uv and ffmpeg installed; `setup.sh` ran to the
  end; all four jobs loaded. The repo is at `~/puckdesk`.
- News sources checked live: Daily Faceoff 32 of 32 teams; Game Day Tweets
  150 posts from 3 pages and 22 goalie guesses for the day on the goalie page;
  DobberHockey and Keeping Karlsson feeds read.
- Whisper (mlx-whisper, large-v3-turbo) works: the first Keeping Karlsson
  episodes are transcribed, and the `media` job works through the queue
  (episodes from the last 21 days).
- Tailscale: the machine is `jacobs-mac-mini` on tailnet `tailbe92b0.ts.net`
  (v1.96.2, auto-update on). Funnel serves port 8765 on 443, and
  `.env` has `PUCKDESK_PUBLIC_HOST=jacobs-mac-mini.tailbe92b0.ts.net`.
  `https://jacobs-mac-mini.tailbe92b0.ts.net/healthz` says `ok`, and the MCP
  endpoint lists all 18 tools.
- LuLu firewall uninstalled.

- Code pushed to `https://github.com/jacoblazdade/puckdesk` (step 1).

Still open, for Jacob:

- Disable key expiry for `jacobs-mac-mini` in the Tailscale admin console
  (Machines → ⋯ → Disable key expiry); otherwise it drops off in about 6 months.
- `sudo pmset -a sleep 0 disksleep 0` and automatic login, if not done yet (step 2).
- Steps 5 to 7 in claude.ai.

## 1. Get the code onto GitHub (5 min)

The copy on the Mac mini (`~/puckdesk`) is the source of truth. The remote is
`https://github.com/jacoblazdade/puckdesk.git` (public), so `.env` stays out
of git (`.gitignore` covers it). Push from the Mac mini with an account that
can write to `jacoblazdade/puckdesk`:

```bash
gh auth login        # as jacoblazdade, or an account added as a collaborator
cd ~/puckdesk && git push -u origin main
```

## 2. Mac mini: basics (10 min)

1. Stay logged in on the Mac mini (the background jobs run in your user
   session). Automatic login in **System Settings → Users & Groups** keeps it
   that way after a power cut.
2. Stop it sleeping:
   ```bash
   sudo pmset -a sleep 0 disksleep 0
   ```
3. Install Homebrew if it isn't there: https://brew.sh. Keep the repo out of
   `~/Documents`, `~/Desktop` and `~/Downloads`: macOS blocks launchd jobs
   there, and `setup.sh` refuses to run from them.
4. Optional but useful: install the Claude desktop app, sign in, open this chat
   and send a message. That links the Mac mini, and Claude can run steps 3 to 5.

## 3. Mac mini: install puckdesk (10 min)

```bash
git clone https://github.com/jacoblazdade/puckdesk.git ~/puckdesk   # or unzip the zip there
cd ~/puckdesk
bash deploy/macmini/setup.sh
```

The script is safe to re-run. It installs Postgres, uv and ffmpeg (a failed
Postgres link is only a warning; it runs `brew postinstall postgresql@17` when
the data directory is missing and stops with the Postgres log if the database
doesn't answer within 30 s), creates `.env` with a random
secret, checks the NHL endpoints and news sources, loads the schedule, last
season's totals, current rosters, Daily Faceoff lines, the Keeping Karlsson
feed and DobberHockey articles, and starts four background jobs:

| Job | When | What |
| --- | --- | --- |
| server | always | the MCP server for Claude |
| nightly | 05:30, 06:45, 12:00 | NHL schedule, rosters, box scores |
| lines | 06:20, 11:30, 16:20, 21:30 | Daily Faceoff lines, PP units, goalies, injuries; Game Day Tweets posts and goalie guesses |
| media | every 2 hours | Keeping Karlsson (transcribed locally), DobberHockey |

The first transcription downloads the Whisper model (about 1.6 GB). It ends
with `ok <- server is up`. Check the news sources with
`uv run puckdesk verify-sources`; it shows what the Daily Faceoff parser found.

## 4. Mac mini: make it reachable (5 min)

Any Tailscale variant works (App Store, Standalone, or Homebrew). puckdesk only
shares a port, and Funnel supports that on all of them. Only sharing files
needs the open source variant.

1. Install Tailscale, or update it if it's already there:
   - Homebrew: `brew install --cask tailscale-app`, or
     `brew upgrade --cask tailscale-app` to update (the cask used to be
     called `tailscale`)
   - Standalone: menu bar icon → Check for Updates
   - App Store: App Store → Updates

   Open it and sign in. If the `tailscale` command is missing, turn on the
   command line tool in the app's settings.
2. If Tailscale was already set up on this Mac, check it before turning on
   Funnel:
   ```bash
   tailscale version        # needs 1.38.3 or newer
   tailscale status         # signed in, to the right tailnet?
   tailscale funnel status  # is anything already shared on 443?
   ```
   - **Don't rename the machine or the tailnet** once the connector is set
     up. The URL comes from both, and renaming breaks the Claude connector.
   - **Disable key expiry** for this machine in the admin console under
     Machines → ⋯. Otherwise it drops off the tailnet when the key expires
     (180 days by default).
   - If something else is already funneled on 443, either reset it
     (`tailscale funnel reset`) or put puckdesk on 8443
     (`tailscale funnel --bg --https=8443 localhost:8765`) and use `host:8443`
     everywhere below.
3. Turn on Funnel for port 8765:
   ```bash
   tailscale funnel --bg 8765
   ```
   The first run prints a link to allow Funnel for your tailnet. Open it,
   allow, then run the command again.
4. Find the public host name (it ends in `.ts.net`; on this Mac mini it's
   `jacobs-mac-mini.tailbe92b0.ts.net`):
   ```bash
   tailscale funnel status
   ```
5. Put it in `~/puckdesk/.env` as `PUCKDESK_PUBLIC_HOST=jacobs-mac-mini.tailbe92b0.ts.net`
   (no `https://`), then restart the server:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.puckdesk.server
   ```
6. Check from your phone: `https://<host>/healthz` should say `ok`.

## 5. Claude: connectors (5 min)

1. **puckdesk:** Settings → Connectors → Add custom connector.
   - Name: `puckdesk` (exactly this; the digest page looks for that name)
   - URL: `https://<host>/mcp/<secret>`, where the secret is in `.env`:
     `grep PUCKDESK_SECRET ~/puckdesk/.env`
2. **Flaim:** sign up at https://flaim.app, connect both Yahoo leagues, and
   add the Flaim connector in Claude. Flaim doesn't return league settings,
   waiver status or week dates, so those are in `leagues.toml` on the Mac mini
   (categories, roster positions, 5 adds a week, 2-day continual rolling
   waivers, the goalie minimum, weeks Monday to Sunday). Edit it if a league
   changes its settings; the server reads it on every call.
3. Open the **Puckdesk Digest** page and allow puckdesk when it asks.

## 6. First digest and tags (5 min)

1. In a Claude chat, paste the prompt from `docs/claude-digest-task.md`.
2. Reload the Puckdesk Digest page. Your leagues appear as tabs:
   **hockey1234123** and **The League**.
3. Set your tags in **Roster tags**: Core for players you'd never drop, Stream
   for rotating slots. Hold is the default.

## 7. Schedule it

Ask Claude to schedule the prompt in `docs/claude-digest-task.md` daily at
06:50, with a push notification when it finishes. Add a 16:50 run for the
goalie check if you want it.

## Beat-writer tweets without X

Game Day Tweets (gamedaytweets.com) already collects beat writers' posts on
lines, starting goalies and injuries, and publishes its own starting-goalie
guesses. The `lines` job reads both four times a day, so there's no X account
or API cost. Posts show up in `search_media`, and the goalie guesses feed the
start probabilities in the projections: confirmed 97%, starter 85%, and a
guess with a caveat (back to back, alternating) 75%. Guesses are stored under
the date shown on the goalie page.

Left Wing Lock and Frozen Tools need subscriptions, so they aren't fetched.

## When Yahoo approves access

1. Create an app at https://developer.yahoo.com/apps/create with redirect URI
   `https://<host>/auth/yahoo/callback` and Fantasy Sports read access.
2. Put `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET` in `.env` and restart the server.
3. Open `https://<host>/auth/yahoo/start?key=<secret>` and sign in to Yahoo.
4. Tell Claude. The Yahoo mapping gets built from real responses
   (`uv run puckdesk yahoo-dump --league-key <key>`), and Flaim is no longer needed.

## If something's off

- Logs: `~/Library/Logs/puckdesk-<job>.log` (server, nightly, lines, media)
- News sources: `uv run puckdesk verify-sources`, then `uv run puckdesk lines`
  (about 150 `tweets_seen`, a goalie guess per team with a posted guess, and
  today's `goalie_date`)
- Postgres won't link or start: a force-linked `libpq` blocks
  `postgresql@17` from linking, and Homebrew then skips creating the data
  directory. Fix: `brew unlink libpq && brew link postgresql@17 && brew postinstall postgresql@17`.
- `tailscale` not on PATH: the binary is
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
- What's loaded: `cd ~/puckdesk && uv run puckdesk status`
- NHL endpoints: `uv run puckdesk verify-nhl`
- Digest page says Offline: the Mac mini is asleep, or Funnel is off
  (`tailscale funnel status`).
