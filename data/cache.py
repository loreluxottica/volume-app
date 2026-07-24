# data/cache.py
# ─────────────────────────────────────────────────────────────────────────────
# Server-side in-memory cache. Reads served from memory after first DB hit.
# Writes call invalidate_* so next read goes back to DB. Every entry also
# expires on a TTL, so changes made OUTSIDE the app (SQL edits to app_access,
# scheduler jobs, a second worker's writes) surface without a restart.
#
# Thread safety: double-checked locking — DB fetch happens inside the lock,
# only one thread fetches per miss. Post-fill reads never acquire the lock.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import threading
from time import monotonic
from typing import Any

import pandas as pd

from data.db import (
    get_access,
    get_chart_weekly,
    get_current_week,
    get_drafts,
    get_gli_extract,
    get_landings_entries,
    get_landings_weekly,
    get_latest_submissions,
    get_setting,
    list_weeks,
)

_lock = threading.Lock()

_WEEK_TTL  = 1800   # weeks / current week: re-check DB every 30 min
_CACHE_TTL = 300    # submissions / drafts / extract / access: 5 min

_current_week: dict[str, Any] | None = None
_current_week_ts: float = 0.0          # monotonic timestamp of last fetch
_weeks: pd.DataFrame | None = None
_weeks_ts: float = 0.0
_access: dict[str, set[str]] | None = None
_access_ts: float = 0.0
_submissions_cache: dict[tuple, pd.DataFrame] = {}   # (year, week_id, site, pl)
_submissions_ts: dict[tuple, float] = {}
_drafts_cache: dict[tuple, pd.DataFrame] = {}        # (year, week_id, site, pl, user)
_drafts_ts: dict[tuple, float] = {}
_gli_cache: dict[tuple, pd.DataFrame] = {}           # (year, week_id)
_gli_ts: dict[tuple, float] = {}
_landings_entries_cache: dict[str, pd.DataFrame] = {}
_landings_entries_ts: dict[str, float] = {}   # TTL: recap values are shared, last write wins
_landings_weekly: dict[int, pd.DataFrame] = {}
_landings_weekly_ts: dict[int, float] = {}   # TTL via _is_stale (chart may lag ≤5 min)
_chart_weekly: dict[int, pd.DataFrame] = {}
_chart_weekly_ts: dict[int, float] = {}
_settings: dict[str, str | None] = {}          # global app_settings key → value
_settings_ts: dict[str, float] = {}


def _is_stale(key, cache_ts: dict) -> bool:
    t = cache_ts.get(key)
    return t is None or (monotonic() - t) > _CACHE_TTL


def cached_current_week() -> dict[str, Any]:
    global _current_week, _current_week_ts
    now = monotonic()
    if _current_week is None or (now - _current_week_ts) > _WEEK_TTL:
        with _lock:
            if _current_week is None or (monotonic() - _current_week_ts) > _WEEK_TTL:
                fresh = get_current_week()
                if _current_week is not None and (
                    (fresh.get("week_id"), fresh.get("year"))
                    != (_current_week.get("week_id"), _current_week.get("year"))
                ):
                    # New week opened — drop stale submissions/drafts/extracts
                    _submissions_cache.clear()
                    _submissions_ts.clear()
                    _drafts_cache.clear()
                    _drafts_ts.clear()
                    _gli_cache.clear()
                    _gli_ts.clear()
                    _landings_weekly.clear()
                    _landings_weekly_ts.clear()
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
    """Access list; TTL'd so SQL edits to app_access surface within 5 min."""
    global _access, _access_ts
    if _access is None or (monotonic() - _access_ts) > _CACHE_TTL:
        with _lock:
            if _access is None or (monotonic() - _access_ts) > _CACHE_TTL:
                _access = get_access()
                _access_ts = monotonic()
    return _access


def cached_submissions(week_id: int, year: int, site: str, product_line: str) -> pd.DataFrame:
    key = (year, week_id, site, product_line)
    if key not in _submissions_cache or _is_stale(key, _submissions_ts):
        with _lock:
            if key not in _submissions_cache or _is_stale(key, _submissions_ts):
                _submissions_cache[key] = get_latest_submissions(week_id, year, site, product_line)
                _submissions_ts[key] = monotonic()
    return _submissions_cache[key]


def cached_drafts(week_id: int, year: int, site: str, product_line: str, user_id: str) -> pd.DataFrame:
    key = (year, week_id, site, product_line, user_id)
    if key not in _drafts_cache or _is_stale(key, _drafts_ts):
        with _lock:
            if key not in _drafts_cache or _is_stale(key, _drafts_ts):
                _drafts_cache[key] = get_drafts(week_id, year, site, product_line, user_id)
                _drafts_ts[key] = monotonic()
    return _drafts_cache[key]


def cached_gli_extract(week_id: int, year: int) -> pd.DataFrame:
    key = (year, week_id)
    if key not in _gli_cache or _is_stale(key, _gli_ts):
        with _lock:
            if key not in _gli_cache or _is_stale(key, _gli_ts):
                _gli_cache[key] = get_gli_extract(week_id, year)
                _gli_ts[key] = monotonic()
    return _gli_cache[key]


def cached_landings_entries(period_key: str) -> pd.DataFrame:
    """Month/Quarter recap values. TTL'd like every other entry: these are shared
    figures with last-write-wins semantics, so a stale snapshot is what a Save
    would write back over a colleague's numbers."""
    if period_key not in _landings_entries_cache or _is_stale(period_key, _landings_entries_ts):
        with _lock:
            if period_key not in _landings_entries_cache or _is_stale(period_key, _landings_entries_ts):
                _landings_entries_cache[period_key] = get_landings_entries(period_key)
                _landings_entries_ts[period_key] = monotonic()
    return _landings_entries_cache[period_key]


def cached_landings_weekly(year: int) -> pd.DataFrame:
    if year not in _landings_weekly or _is_stale(year, _landings_weekly_ts):
        with _lock:
            if year not in _landings_weekly or _is_stale(year, _landings_weekly_ts):
                _landings_weekly[year] = get_landings_weekly(year)
                _landings_weekly_ts[year] = monotonic()
    return _landings_weekly[year]


def cached_chart_weekly(year: int) -> pd.DataFrame:
    if year not in _chart_weekly or _is_stale(year, _chart_weekly_ts):
        with _lock:
            if year not in _chart_weekly or _is_stale(year, _chart_weekly_ts):
                _chart_weekly[year] = get_chart_weekly(year)
                _chart_weekly_ts[year] = monotonic()
    return _chart_weekly[year]


def invalidate_chart_weekly(year: int) -> None:
    """Call after save_chart_weekly_manual."""
    with _lock:
        _chart_weekly.pop(year, None)
        _chart_weekly_ts.pop(year, None)


def cached_setting(key: str, default: str | None = None) -> str | None:
    """Global app setting, TTL'd so an admin's change surfaces within 5 min
    (and immediately in the single-worker process after invalidate_setting)."""
    if key not in _settings or _is_stale(key, _settings_ts):
        with _lock:
            if key not in _settings or _is_stale(key, _settings_ts):
                _settings[key] = get_setting(key, default)
                _settings_ts[key] = monotonic()
    val = _settings.get(key)
    return default if val is None else val


def invalidate_setting(key: str) -> None:
    """Call after set_setting."""
    with _lock:
        _settings.pop(key, None)
        _settings_ts.pop(key, None)


def invalidate_landings_entries(period_key: str) -> None:
    """Call after save_landings_entries."""
    with _lock:
        _landings_entries_cache.pop(period_key, None)
        _landings_entries_ts.pop(period_key, None)


def invalidate_submissions(week_id: int, year: int, site: str, product_line: str) -> None:
    """Call after submit_row — also drops GLOBAL extract since it changed."""
    with _lock:
        _submissions_cache.pop((year, week_id, site, product_line), None)
        _submissions_ts.pop((year, week_id, site, product_line), None)
        _gli_cache.pop((year, week_id), None)
        _gli_ts.pop((year, week_id), None)
        # A grid submit can change the Landings chart / WK block
        _landings_weekly.clear()
        _landings_weekly_ts.clear()


def invalidate_drafts(week_id: int, year: int, site: str, product_line: str, user_id: str) -> None:
    """Call after save_draft or delete_draft."""
    with _lock:
        _drafts_cache.pop((year, week_id, site, product_line, user_id), None)
        _drafts_ts.pop((year, week_id, site, product_line, user_id), None)


def invalidate_access() -> None:
    global _access
    with _lock:
        _access = None


def invalidate_all() -> None:
    """Full cache clear — call when a new week is opened."""
    global _current_week, _access, _weeks
    with _lock:
        _submissions_cache.clear()
        _submissions_ts.clear()
        _drafts_cache.clear()
        _drafts_ts.clear()
        _gli_cache.clear()
        _gli_ts.clear()
        _landings_entries_cache.clear()
        _landings_entries_ts.clear()
        _landings_weekly.clear()
        _landings_weekly_ts.clear()
        _chart_weekly.clear()
        _chart_weekly_ts.clear()
        _current_week = None
        _access = None
        _weeks = None
