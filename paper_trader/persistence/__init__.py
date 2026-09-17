"""Persistence layer: local JSON storage for sessions and app settings."""

from .store import SessionInfo, Store, StoreError

__all__ = ["Store", "SessionInfo", "StoreError"]
