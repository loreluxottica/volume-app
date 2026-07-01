# data/cache.py
# ─────────────────────────────────────────────────────────────────────────────
# Server-side in-memory cache. Reads served from memory after first DB hit.
# Writes call invalidate_* so next read goes back to DB.
#
# Thread safety: double-checked locking — DB fetch happens inside the lock,
# only one thread fetches per miss. Post-fill reads never acquire the lock.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import threading
import time
from time import monotonic
from typing import Any

import pandas as pd

from data.db import (
    get_access,
    get_current_week,
    get_drafts,
    get_gli_extract,
    get_latest_submissions,
    list_weeks,
)

_lock = threading.Lock()

_current_week: dict[str, Any] | None = None
_current_week_ts: float = 0.0          # monotonic timestamp of last fetch
_WEEK_TTL = 1800                        # re-check DB every 30 min
_weeks: pd.DataFrame | None = None
_weeks_ts: float = 0.0
_access: dict[str, set[str]] | None = None
_submissions_cache: dict[tuple, pd.DataFrame] = {}
_drafts_cache: dict[tuple, pd.DataFrame] = {}
_gli_cache: dict[int, pd.DataFrame] = {}


def cached_current_week() -> dict[str, Any]:
    global _current_week, _current_week_ts
    now = monotonic()
    if _current_week is None or (now - _current_week_ts) > _WEEK_TTL:
        with _lock:
            if _current_week is None or (monotonic() - _current_week_ts) > _WEEK_TTL:
                fresh = get_current_week()
                if _current_week is not None and fresh.get("week_id") != _current_week.get("week_id"):
                    # New week opened — drop stale submissions/drafts/extracts
                    _submissions_cache.clear()
                    _drafts_cache.clear()
                    _gli_cache.clear()
                _current_week = fresh
                _current_week_ts = monotonic()
    return _current_week


def cached_weeks() -> pd.DataFrame:
    """All weeks (open + past) for the back-selector; re-checked every 30 min."""
    global _weeks, _weeks_ts
    now = monotonic()
    if _weeks is None or (now - _weeks_ts) > _WEEK_TTL:
        with _lock:
            if _weeks is None or (monotonic() - _weeks_ts) > _WEEK_TTL:
                _weeks = list_weeks()
                _weeks_ts = monotonic()
    return _weeks


def cached_access() -> dict[str, set[str]]:
    global _access
    if _access is None:
        with _lock:
            if _access is None:
                _access = get_access()
    return _access


def cached_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    key = (week_id, site, product_line)
    if key not in _submissions_cache:
        with _lock:
            if key not in _submissions_cache:
                _submissions_cache[key] = get_latest_submissions(week_id, site, product_line)
    return _submissions_cache[key]


def cached_drafts(week_id: int, site: str, product_line: str, user_id: str) -> pd.DataFrame:
    key = (week_id, site, product_line, user_id)
    if key not in _drafts_cache:
        with _lock:
            if key not in _drafts_cache:
                _drafts_cache[key] = get_drafts(week_id, site, product_line, user_id)
    return _drafts_cache[key]


def cached_gli_extract(week_id: int) -> pd.DataFrame:
    if week_id not in _gli_cache:
        with _lock:
            if week_id not in _gli_cache:
                _gli_cache[week_id] = get_gli_extract(week_id)
    return _gli_cache[week_id]


def invalidate_submissions(week_id: int, site: str, product_line: str) -> None:
    """Call after submit_row — also drops GLOBAL extract since it changed."""
    with _lock:
        _submissions_cache.pop((week_id, site, product_line), None)
        _gli_cache.pop(week_id, None)


def invalidate_drafts(week_id: int, site: str, product_line: str, user_id: str) -> None:
    """Call after save_draft or delete_draft."""
    with _lock:
        _drafts_cache.pop((week_id, site, product_line, user_id), None)


def invalidate_access() -> None:
    global _access
    with _lock:
        _access = None


def invalidate_all() -> None:
    """Full cache clear — call when a new week is opened."""
    global _current_week, _access, _weeks
    with _lock:
        _submissions_cache.clear()
        _drafts_cache.clear()
        _gli_cache.clear()
        _current_week = None
        _access = None
        _weeks = None


_CACHE_TTL = 300  # secondi

def _is_stale(key, cache_ts: dict) -> bool:
    t = cache_ts.get(key)
    return t is None or (time.monotonic() - t) > _CACHE_TTL
