#!/usr/bin/env bash
# One-time setup of puckdesk on an always-on Mac (Apple Silicon, Homebrew).
# Run from the repository root:  bash deploy/macmini/setup.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

echo "==> Homebrew packages"
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
brew list postgresql@17 >/dev/null 2>&1 || brew install postgresql@17
command -v uv >/dev/null || brew install uv
command -v ffmpeg >/dev/null || brew install ffmpeg   # audio decoding for Whisper
brew services start postgresql@17
PG_BIN="$(brew --prefix postgresql@17)/bin"
sleep 2
"$PG_BIN/createdb" puckdesk 2>/dev/null || echo "database puckdesk already exists"

echo "==> Python environment (with local Whisper)"
uv sync --extra mac

if [ ! -f .env ]; then
  echo "==> Writing .env"
  SECRET="$(uv run puckdesk new-secret)"
  cat > .env <<EOF
DATABASE_URL=postgresql:///puckdesk
PUCKDESK_SECRET=$SECRET
PUCKDESK_HOST=127.0.0.1
PUCKDESK_PORT=8765
# Set after Tailscale Funnel is on, e.g. mac-mini.tail1234.ts.net (no https://)
PUCKDESK_PUBLIC_HOST=
# After Yahoo approves API access:
YAHOO_CLIENT_ID=
YAHOO_CLIENT_SECRET=
EOF
fi

echo "==> Database schema and NHL data"
uv run puckdesk init-db
uv run puckdesk verify-nhl
uv run puckdesk sync-schedule
uv run puckdesk sync-priors
uv run puckdesk sync-rosters
uv run puckdesk status

echo "==> News sources"
uv run puckdesk verify-sources
uv run puckdesk lines
uv run puckdesk media --no-transcribe   # the background job transcribes; the first run downloads the Whisper model (~1.6 GB)

echo "==> Background jobs (launchd)"
UV="$(command -v uv)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$HOME/Library/Logs"
for name in server nightly media lines; do
  src="deploy/macmini/com.puckdesk.$name.plist"
  dst="$AGENTS/com.puckdesk.$name.plist"
  sed -e "s#__REPO__#$REPO#g" -e "s#__UV__#$UV#g" -e "s#__HOME__#$HOME#g" "$src" > "$dst"
  launchctl bootout "gui/$(id -u)" "$dst" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dst"
done

sleep 3
curl -fsS "http://127.0.0.1:8765/healthz" && echo " <- server is up"

cat <<EOF

Next steps
1. Keep the Mac awake:        sudo pmset -a sleep 0 disksleep 0
2. Tailscale:                 install the Tailscale app, sign in, then run
                              tailscale funnel --bg 8765
                              (the first run prints a link to enable Funnel for your tailnet)
3. Put the Funnel host name in .env as PUCKDESK_PUBLIC_HOST, then restart the server:
                              launchctl kickstart -k gui/$(id -u)/com.puckdesk.server
4. Add the connector in Claude (Settings > Connectors > Add custom connector):
                              https://<PUCKDESK_PUBLIC_HOST>/mcp/<PUCKDESK_SECRET from .env>
EOF
