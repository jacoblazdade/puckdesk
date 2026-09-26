"""Optional: recent posts from a few X accounts through the paid X API.

Off unless X_BEARER_TOKEN is set. Reads are capped per month (sources.toml),
because X charges per post read.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .store import Store

API = "https://api.x.com/2"
log = logging.getLogger("puckdesk.x")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def poll(store: Store, token: str, accounts: list[str], monthly_cap: int = 4000, per_account: int = 20) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    month = _month()
    stored = 0
    with store.conn() as c, httpx.Client(headers=headers, timeout=30) as http:
        c.execute("insert into x_usage (month, reads) values (%s, 0) on conflict (month) do nothing", (month,))
        used = c.execute("select reads from x_usage where month = %s", (month,)).fetchone()["reads"]
        for acc in accounts:
            if used + per_account > monthly_cap:
                log.warning("X monthly read cap reached (%s/%s)", used, monthly_cap)
                break
            st = c.execute("select user_id, since_id from x_state where account = %s", (acc,)).fetchone()
            user_id = st["user_id"] if st else None
            if not user_id:
                r = http.get(f"{API}/users/by/username/{acc}")
                r.raise_for_status()
                user_id = r.json()["data"]["id"]
                c.execute(
                    "insert into x_state (account, user_id) values (%s, %s) on conflict (account) do update set user_id = excluded.user_id",
                    (acc, user_id),
                )
            params = {"max_results": per_account, "tweet.fields": "created_at", "exclude": "retweets,replies"}
            if st and st["since_id"]:
                params["since_id"] = st["since_id"]
            r = http.get(f"{API}/users/{user_id}/tweets", params=params)
            r.raise_for_status()
            body = r.json()
            data = body.get("data", [])
            used += len(data)
            for t in data:
                c.execute(
                    """insert into posts (id, account, posted_at, text, url) values (%s, %s, %s, %s, %s)
                       on conflict (id) do nothing""",
                    (t["id"], acc, t.get("created_at"), t["text"], f"https://x.com/{acc}/status/{t['id']}"),
                )
                stored += 1
            newest = body.get("meta", {}).get("newest_id")
            if newest:
                c.execute("update x_state set since_id = %s where account = %s", (newest, acc))
        c.execute("update x_usage set reads = %s where month = %s", (used, month))
    return {"stored": stored, "reads_this_month": used, "cap": monthly_cap}
