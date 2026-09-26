"""Settings from environment variables, with an optional .env file."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    database_url: str
    secret: str
    host: str
    port: int
    public_host: str | None
    yahoo_client_id: str | None
    yahoo_client_secret: str | None

    @property
    def mcp_path(self) -> str:
        return f"/mcp/{self.secret}"

    @property
    def public_url(self) -> str | None:
        return f"https://{self.public_host}{self.mcp_path}" if self.public_host else None

    @property
    def yahoo_redirect_uri(self) -> str | None:
        return f"https://{self.public_host}/auth/yahoo/callback" if self.public_host else None


def settings() -> Settings:
    load_dotenv(os.environ.get("PUCKDESK_ENV", ".env"))
    secret = os.environ.get("PUCKDESK_SECRET")
    if not secret:
        raise SystemExit(
            "PUCKDESK_SECRET is not set. Generate one with:\n  python -c \"import secrets; print(secrets.token_urlsafe(24))\""
        )
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql:///puckdesk"),
        secret=secret,
        host=os.environ.get("PUCKDESK_HOST", "127.0.0.1"),
        port=int(os.environ.get("PUCKDESK_PORT", "8765")),
        public_host=os.environ.get("PUCKDESK_PUBLIC_HOST") or None,
        yahoo_client_id=os.environ.get("YAHOO_CLIENT_ID") or None,
        yahoo_client_secret=os.environ.get("YAHOO_CLIENT_SECRET") or None,
    )


def new_secret() -> str:
    return secrets.token_urlsafe(24)
