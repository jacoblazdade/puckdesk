"""Yahoo Fantasy Sports API: OAuth 2.0 and raw reads.

Yahoo now reviews every API application and grants read-only access. Until
approval arrives, league data comes from a read-only connector in Claude
(for example Flaim) and is passed to the engine as a LeagueState.

Once approved: set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET, open
https://<public host>/auth/yahoo/start?key=<PUCKDESK_SECRET> once to sign in,
then `puckdesk yahoo-dump` saves raw league JSON to build the LeagueState
mapping against real responses.
"""

from __future__ import annotations

import base64
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .store import Store

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API = "https://fantasysports.yahooapis.com/fantasy/v2"

_pending_states: dict[str, float] = {}


def authorize_url(client_id: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(16)
    _pending_states[state] = time.time()
    q = urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state})
    return f"{AUTH_URL}?{q}"


def check_state(state: str | None) -> bool:
    created = _pending_states.pop(state or "", None)
    return created is not None and time.time() - created < 900


def _basic(client_id: str, client_secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _save(store: Store, body: dict) -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 3600)) - 60)
    with store.conn() as c:
        c.execute(
            """insert into oauth_tokens (provider, access_token, refresh_token, expires_at) values ('yahoo', %s, %s, %s)
               on conflict (provider) do update set access_token = excluded.access_token,
                 refresh_token = excluded.refresh_token, expires_at = excluded.expires_at, updated_at = now()""",
            (body["access_token"], body["refresh_token"], expires),
        )


def exchange_code(store: Store, client_id: str, client_secret: str, redirect_uri: str, code: str) -> None:
    r = httpx.post(
        TOKEN_URL,
        headers={"Authorization": _basic(client_id, client_secret)},
        data={"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code},
        timeout=30,
    )
    r.raise_for_status()
    _save(store, r.json())


def access_token(store: Store, client_id: str, client_secret: str, redirect_uri: str) -> str:
    with store.conn() as c:
        row = c.execute("select * from oauth_tokens where provider = 'yahoo'").fetchone()
    if not row:
        raise RuntimeError("Not signed in to Yahoo yet: open /auth/yahoo/start?key=<secret> on the server.")
    if row["expires_at"] > datetime.now(timezone.utc):
        return row["access_token"]
    r = httpx.post(
        TOKEN_URL,
        headers={"Authorization": _basic(client_id, client_secret)},
        data={"grant_type": "refresh_token", "redirect_uri": redirect_uri, "refresh_token": row["refresh_token"]},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    body.setdefault("refresh_token", row["refresh_token"])
    _save(store, body)
    return body["access_token"]


def get(store: Store, client_id: str, client_secret: str, redirect_uri: str, path: str) -> dict:
    token = access_token(store, client_id, client_secret, redirect_uri)
    r = httpx.get(f"{API}/{path.lstrip('/')}", params={"format": "json"}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


# Reads worth saving once access is approved, to build the LeagueState mapping.
DUMP_PATHS = [
    "users;use_login=1/games;game_keys=nhl/leagues",
    "league/{league_key}/settings",
    "league/{league_key}/teams/roster",
    "league/{league_key}/scoreboard",
    "league/{league_key}/players;status=FA;sort=AR;count=25",
    "league/{league_key}/players;status=W;count=25",
    "league/{league_key}/transactions;count=25",
]
