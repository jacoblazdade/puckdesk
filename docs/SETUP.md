# Setup at home

About 45 minutes, most of it waiting for installs. Steps 3 to 5 can also be
done by Claude once the Mac mini is linked to the chat (step 2).

Already done: the Yahoo API application is in review (1 to 2 weeks), the code
is in this repo, and the **Puckdesk Digest** page exists in Claude (it shows
sample data until the connector is added).

## 1. Laptop: get the code onto GitHub (5 min)

Pick one:

- **Let Claude push it:** claude.ai **Settings → Connectors → GitHub**, connect,
  and give it access to `jacoblazdade/puckdesk`. Then tell Claude it's connected.
- **Push it yourself:** unzip `puckdesk.zip`, then
  ```bash
  cd puckdesk
  git push -u origin main
  ```

## 2. Mac mini: basics (10 min)

1. Stay logged in on the Mac mini (the background jobs run in your user
   session). Automatic login in **System Settings → Users & Groups** keeps it
   that way after a power cut.
2. Stop it sleeping:
   ```bash
   sudo pmset -a sleep 0 disksleep 0
   ```
3. Install Homebrew if it isn't there: https://brew.sh
4. Optional but useful: install the Claude desktop app, sign in, open this chat
   and send a message. That links the Mac mini, and Claude can run steps 3 to 5.

## 3. Mac mini: install puckdesk (10 min)

```bash
git clone https://github.com/jacoblazdade/puckdesk.git ~/puckdesk   # or unzip the zip there
cd ~/puckdesk
bash deploy/macmini/setup.sh
```

The script installs Postgres, uv and ffmpeg, creates `.env` with a random
secret, checks the NHL endpoints and news sources, loads the schedule, last
season's totals, current rosters, Daily Faceoff lines, the Keeping Karlsson
feed and DobberHockey articles, and starts four background jobs:

| Job | When | What |
| --- | --- | --- |
| server | always | the MCP server for Claude |
| nightly | 05:30, 06:45, 12:00 | NHL schedule, rosters, box scores |
| lines | 06:20, 11:30, 16:20, 21:30 | Daily Faceoff lines, PP units, goalies, injuries |
| media | every 2 hours | Keeping Karlsson (transcribed locally), DobberHockey, X posts |

The first transcription downloads the Whisper model (about 1.6 GB). It ends
with `ok <- server is up`. Check the news sources with
`uv run puckdesk verify-sources`; it shows what the Daily Faceoff parser found.

## 4. Mac mini: make it reachable (5 min)

1. Install Tailscale (`brew install --cask tailscale` or the App Store), open
   it and sign in. If the `tailscale` command is missing, use the menu bar
   app's option to install the command line tool.
2. Turn on Funnel for port 8765:
   ```bash
   tailscale funnel --bg 8765
   ```
   The first run prints a link to allow Funnel for your tailnet. Open it,
   allow, then run the command again.
3. Find the public host name (it ends in `.ts.net`):
   ```bash
   tailscale funnel status
   ```
4. Put it in `~/puckdesk/.env` as `PUCKDESK_PUBLIC_HOST=mac-mini.tailXXXX.ts.net`
   (no `https://`), then restart the server:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.puckdesk.server
   ```
5. Check from your phone: `https://<host>/healthz` should say `ok`.

## 5. Claude: connectors (5 min)

1. **puckdesk:** Settings → Connectors → Add custom connector.
   - Name: `puckdesk` (exactly this; the digest page looks for that name)
   - URL: `https://<host>/mcp/<secret>`, where the secret is in `.env`:
     `grep PUCKDESK_SECRET ~/puckdesk/.env`
2. **Flaim:** sign up at https://flaim.app, connect both Yahoo leagues, and
   add the Flaim connector in Claude.
3. Open the **Puckdesk Digest** page and allow puckdesk when it asks.

## 6. First digest and tags (5 min)

1. In a Claude chat, paste the prompt from `docs/claude-digest-task.md`.
2. Reload the Puckdesk Digest page. Your leagues appear as tabs.
3. Set your tags in **Roster tags**: Core for players you'd never drop, Stream
   for rotating slots. Hold is the default.

## 7. Schedule it

Ask Claude to schedule the prompt in `docs/claude-digest-task.md` daily at
06:50, with a push notification when it finishes. Add a 16:50 run for the
goalie check if you want it.

## Optional: X posts

The lineup account is @GameDayLines (line combinations and starting goalies
from beat writers). The accounts are listed in `sources.toml`. X charges
$0.005 per post read, so this stays off until you add a token:

1. Create a developer account at https://developer.x.com and an app with
   pay-per-use billing.
2. Put its bearer token in `.env` as `X_BEARER_TOKEN=...`. The media job then
   polls the accounts every two hours, capped at `monthly_read_cap` reads
   (4,000 reads is about $20 a month).

Left Wing Lock and Frozen Tools need subscriptions, so they aren't fetched.

## When Yahoo approves access

1. Create an app at https://developer.yahoo.com/apps/create with redirect URI
   `https://<host>/auth/yahoo/callback` and Fantasy Sports read access.
2. Put `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET` in `.env` and restart the server.
3. Open `https://<host>/auth/yahoo/start?key=<secret>` and sign in to Yahoo.
4. Tell Claude. The Yahoo mapping gets built from real responses
   (`uv run puckdesk yahoo-dump --league-key <key>`), and Flaim is no longer needed.

## If something's off

- Logs: `~/Library/Logs/puckdesk-server.log` and `puckdesk-nightly.log`
- What's loaded: `cd ~/puckdesk && uv run puckdesk status`
- NHL endpoints: `uv run puckdesk verify-nhl`
- Digest page says Offline: the Mac mini is asleep, or Funnel is off
  (`tailscale funnel status`).
