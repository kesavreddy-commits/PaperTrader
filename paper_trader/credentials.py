"""Alpaca credential loading — from environment or a local file.

Precedence:
    1. Environment variables ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``
       (Alpaca's own conventional names), optionally ``APCA_API_BASE_URL`` /
       ``APCA_API_DATA_URL``.
    2. ``~/.paper_trader/credentials.json`` (the app writes here; chmod 600).

Secrets are never stored in the repository. The file lives under the app-home
directory alongside sessions/settings.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass

from .config import app_home

DEFAULT_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_DATA_URL = "https://data.alpaca.markets"


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    key_id: str
    secret_key: str
    base_url: str = DEFAULT_BASE_URL
    data_url: str = DEFAULT_DATA_URL
    paper: bool = True

    @property
    def is_paper(self) -> bool:
        """True only for Alpaca's paper endpoint — a live host is never 'paper'."""
        return "paper-api" in self.base_url.lower()


def credentials_path():
    return app_home() / "credentials.json"


def load_alpaca() -> AlpacaCredentials | None:
    """Return Alpaca credentials from env or disk, or ``None`` if unset."""
    env_key = os.environ.get("APCA_API_KEY_ID")
    env_sec = os.environ.get("APCA_API_SECRET_KEY")
    if env_key and env_sec:
        return AlpacaCredentials(
            key_id=env_key,
            secret_key=env_sec,
            base_url=os.environ.get("APCA_API_BASE_URL", DEFAULT_BASE_URL),
            data_url=os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL),
            paper=True,
        )

    path = credentials_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None

    alp = data.get("alpaca") if isinstance(data, dict) else None
    if not alp or not alp.get("key_id") or not alp.get("secret_key"):
        return None
    return AlpacaCredentials(
        key_id=alp["key_id"],
        secret_key=alp["secret_key"],
        base_url=alp.get("base_url", DEFAULT_BASE_URL),
        data_url=alp.get("data_url", DEFAULT_DATA_URL),
        paper=bool(alp.get("paper", True)),
    )


def save_alpaca(key_id: str, secret_key: str, *, paper: bool = True,
                base_url: str = DEFAULT_BASE_URL, data_url: str = DEFAULT_DATA_URL) -> None:
    """Persist Alpaca credentials to the local file with 0600 permissions."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "alpaca": {
            "key_id": key_id.strip(),
            "secret_key": secret_key.strip(),
            "base_url": base_url,
            "data_url": data_url,
            "paper": paper,
        }
    }
    # Create the file 0600 up front — writing first and chmod-ing afterwards
    # would leave the secret world-readable for the duration of the write.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # tighten a pre-existing file
    except OSError:
        pass


def is_paper_endpoint(base_url: str) -> bool:
    """True if ``base_url`` points at Alpaca's paper-trading host."""
    return "paper-api" in (base_url or "").lower()


def has_alpaca() -> bool:
    return load_alpaca() is not None
