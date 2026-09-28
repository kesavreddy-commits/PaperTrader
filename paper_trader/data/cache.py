"""A tiny thread-safe TTL cache.

This is the first line of defence against provider rate limits: repeated
requests for the same key within its TTL return the cached value instead of
hitting the network. It is intentionally minimal — no background eviction
thread, just lazy expiry on access.
"""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Map of ``key -> (value, expires_at)`` with per-entry time-to-live."""

    def __init__(self, ttl_seconds: float, max_entries: int = 512) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: dict[str, tuple[T, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> T | None:
        """Return the cached value for ``key`` if present and unexpired."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if now >= expires_at:
                # Lazily drop the stale entry.
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: T, ttl: float | None = None) -> None:
        """Cache ``value`` under ``key`` for its TTL."""
        ttl = self._ttl if ttl is None else ttl
        now = time.monotonic()
        with self._lock:
            self._store[key] = (value, now + ttl)
            if len(self._store) > self._max_entries:
                self._evict(now)

    def _evict(self, now: float) -> None:
        """Drop expired entries (then the oldest) so the map stays bounded.

        Expiry is otherwise lazy — a key that is never read again would keep its
        entry forever, which leaks over a long session of symbol searches.
        Callers hold the lock.
        """
        for key in [k for k, (_, exp) in self._store.items() if now >= exp]:
            del self._store[key]
        if len(self._store) > self._max_entries:
            surplus = len(self._store) - self._max_entries
            for key in sorted(self._store, key=lambda k: self._store[k][1])[:surplus]:
                del self._store[key]

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
