"""Local persistence: sessions and settings as JSON on disk.

Everything lives under :func:`config.app_home` (``~/.paper_trader`` by default):

    ~/.paper_trader/
        settings.json            app-level settings (theme, data source, last session)
        sessions/<id>.json       one file per trading session

Writes are atomic (write to a temp file, then ``os.replace``) so a crash mid-save
can never corrupt an existing session. Loads are defensive: a damaged file raises
:class:`StoreError` rather than taking the app down, and :meth:`list_sessions`
simply skips unreadable files.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import (
    DEFAULT_THEME,
    app_home,
    sessions_dir,
    settings_path,
)
from ..core.models import Session


class StoreError(Exception):
    """Raised when a session/settings file cannot be read or written."""


@dataclass(slots=True)
class SessionInfo:
    """Lightweight metadata for the session picker (avoids loading everything)."""

    id: str
    name: str
    updated_at: datetime
    starting_balance: float
    cash: float
    num_trades: int
    num_positions: int


class Store:
    """Reads and writes sessions and settings under the app home directory."""

    def __init__(self) -> None:
        self._ensure_dirs()

    # ------------------------------------------------------------------ #
    # Directory / path helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ensure_dirs() -> None:
        sessions_dir().mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _session_path(session_id: str):
        return sessions_dir() / f"{session_id}.json"

    @staticmethod
    def _atomic_write(path, text: str) -> None:
        """Write ``text`` to ``path`` atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # NamedTemporaryFile in the same dir guarantees os.replace is atomic.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            # Clean up the temp file on any failure.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #
    def save_session(self, session: Session) -> None:
        try:
            payload = json.dumps(session.to_dict(), indent=2)
            self._atomic_write(self._session_path(session.id), payload)
        except (OSError, TypeError, ValueError) as exc:
            raise StoreError(f"Could not save session '{session.name}': {exc}") from exc

    def load_session(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return Session.from_dict(data)
        except FileNotFoundError as exc:
            raise StoreError(f"Session '{session_id}' does not exist.") from exc
        except (OSError, ValueError, KeyError) as exc:
            raise StoreError(f"Session '{session_id}' is corrupted: {exc}") from exc

    def delete_session(self, session_id: str) -> None:
        try:
            self._session_path(session_id).unlink(missing_ok=True)
        except OSError as exc:
            raise StoreError(f"Could not delete session '{session_id}': {exc}") from exc

    def list_sessions(self) -> list[SessionInfo]:
        """Return metadata for all saved sessions, newest first.

        Unreadable/corrupt files are skipped rather than aborting the listing.
        """
        infos: list[SessionInfo] = []
        for path in sessions_dir().glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                infos.append(
                    SessionInfo(
                        id=d.get("id", path.stem),
                        name=d.get("name", path.stem),
                        updated_at=_parse_dt(d.get("updated_at")),
                        starting_balance=float(d.get("starting_balance", 0.0)),
                        cash=float(d.get("cash", 0.0)),
                        num_trades=len(d.get("trades", [])),
                        num_positions=len(d.get("positions", {})),
                    )
                )
            except (OSError, ValueError):
                continue  # skip corrupt file
        infos.sort(key=lambda i: i.updated_at, reverse=True)
        return infos

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #
    def load_settings(self) -> dict:
        try:
            with open(settings_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return self._default_settings()

    def save_settings(self, settings: dict) -> None:
        try:
            self._atomic_write(settings_path(), json.dumps(settings, indent=2))
        except (OSError, TypeError, ValueError) as exc:
            raise StoreError(f"Could not save settings: {exc}") from exc

    def get_setting(self, key: str, default=None):
        return self.load_settings().get(key, default)

    def set_setting(self, key: str, value) -> None:
        settings = self.load_settings()
        settings[key] = value
        self.save_settings(settings)

    @staticmethod
    def _default_settings() -> dict:
        return {
            "theme": DEFAULT_THEME,
            "data_source": "yahoo",
            "last_session_id": None,
        }

    @property
    def home(self):
        return app_home()


# Sessions are sorted by this value, so every result must be timezone-aware —
# mixing naive and aware datetimes makes the comparison raise TypeError and takes
# the whole session listing (and the app's startup fallback) down with it.
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _parse_dt(text) -> datetime:
    if not text:
        return _EPOCH
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return _EPOCH
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
