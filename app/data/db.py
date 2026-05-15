# data/db.py
# ─────────────────────────────────────────────────────────────────────────────
# Database layer: reads from and writes to Delta Lake tables via the
# Databricks SQL connector.  All SQL is isolated here — no other module
# touches the DB directly.
#
# Tables expected in Unity Catalog:
#   gli.volumes.weeks
#   gli.volumes.submissions
#   gli.volumes.drafts
#   gli.volumes.gli_extract   (SQL view — read only)
#
# Connection is established once at module import and reused across requests.
# In Databricks Apps the DATABRICKS_HOST and DATABRICKS_TOKEN env vars are
# injected automatically — no manual configuration needed.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from databricks import sql

# ── Connection ────────────────────────────────────────────────────────────────

_conn: sql.client.Connection | None = None


def _get_conn() -> sql.client.Connection:
    global _conn
    if _conn is None:
        _conn = sql.connect(
            server_hostname=os.environ["DATABRICKS_HOST"],
            http_path=os.environ["DATABRICKS_HTTP_PATH"],   # SQL Warehouse HTTP path
            access_token=os.environ["DATABRICKS_TOKEN"],
        )
    return _conn


def _exec(query: str, params: list | None = None) -> pd.DataFrame:
    """Execute a SELECT and return a DataFrame."""
    with _get_conn().cursor() as cur:
        cur.execute(query, params or [])
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def _run(query: str, params: list | None = None) -> None:
    """Execute an INSERT/UPDATE with no return value."""
    with _get_conn().cursor() as cur:
        cur.execute(query, params or [])


# ── weeks ─────────────────────────────────────────────────────────────────────

def get_current_week() -> dict[str, Any]:
    """Return the most recent open week record."""
    df = _exec(
        "SELECT week_id, year, created_at FROM gli.volumes.weeks "
        "WHERE is_open = TRUE ORDER BY year DESC, week_id DESC LIMIT 1"
    )
    if df.empty:
        raise RuntimeError("No open week found in gli.volumes.weeks")
    return df.iloc[0].to_dict()


def create_week(week_id: int, year: int) -> None:
    """Insert a new week (called by the Tuesday scheduler job)."""
    _run(
        "INSERT INTO gli.volumes.weeks (week_id, year, created_at, is_open) "
        "VALUES (?, ?, ?, TRUE)",
        [week_id, year, datetime.now(timezone.utc)],
    )


# ── submissions ───────────────────────────────────────────────────────────────

def get_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    """
    Return all submission rows for a given week/site/product_line.
    Includes is_amendment and official_log flags so the UI can show history.
    """
    return _exec(
        """
        SELECT submission_id, timestamp, submission_type, channel,
               value_kpcs, is_zero_flagged, official_log,
               comment_preset, comment_other, is_amendment
        FROM gli.volumes.submissions
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
        ORDER BY timestamp ASC
        """,
        [week_id, site, product_line],
    )


def get_latest_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    """
    Return only the authoritative (official_log = TRUE) submission per
    (submission_type, channel) key.  This is what the UI displays.
    """
    return _exec(
        """
        SELECT submission_type, channel, value_kpcs,
               is_zero_flagged, comment_preset, comment_other
        FROM gli.volumes.submissions
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
          AND official_log = TRUE
        """,
        [week_id, site, product_line],
    )


def submit_row(
    week_id: int,
    site: str,
    product_line: str,
    user_id: str,
    submission_type: str,
    values: dict[str, float | None],        # {channel_id: value_kpcs}
    zero_flags: dict[str, bool],            # {channel_id: is_zero_flagged}
    comments: dict[str, dict] | None = None,  # {channel_id: {presets:[..], others:..}}
) -> None:
    """
    Append one row per channel to gli.volumes.submissions.
    Marks previous official_log rows for the same key as FALSE before inserting.
    Uses a single transaction-like batch (Delta Lake ACID guarantees atomicity).
    """
    now = datetime.now(timezone.utc)

    # Step 1: un-mark previous official rows for this (week, site, pl, type)
    _run(
        """
        UPDATE gli.volumes.submissions
        SET official_log = FALSE
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
          AND submission_type = ?
          AND official_log = TRUE
        """,
        [week_id, site, product_line, submission_type],
    )

    # Step 2: insert one row per channel
    for channel, value in values.items():
        if value is None and not zero_flags.get(channel):
            continue  # skip truly empty N/A cells
        comment_data = (comments or {}).get(channel, {})
        presets = ",".join(comment_data.get("presets", []))
        others  = comment_data.get("others", "") or ""

        _run(
            """
            INSERT INTO gli.volumes.submissions
              (submission_id, timestamp, week_id, site, product_line,
               user_id, submission_type, channel, value_kpcs,
               is_zero_flagged, official_log,
               comment_preset, comment_other, is_amendment, ref_submission_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?, FALSE, NULL)
            """,
            [
                str(uuid.uuid4()), now, week_id, site, product_line,
                user_id, submission_type, channel, value,
                zero_flags.get(channel, False),
                presets, others,
            ],
        )


# ── drafts ────────────────────────────────────────────────────────────────────

def get_draft(week_id: int, site: str, product_line: str,
              submission_type: str, user_id: str) -> pd.DataFrame:
    """Return draft rows for a given key (one row per channel)."""
    return _exec(
        """
        SELECT channel, value_kpcs, is_zero_flagged, comment_preset, comment_other
        FROM gli.volumes.drafts
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
          AND submission_type = ?
          AND user_id = ?
        """,
        [week_id, site, product_line, submission_type, user_id],
    )


def save_draft(
    week_id: int,
    site: str,
    product_line: str,
    user_id: str,
    submission_type: str,
    values: dict[str, float | None],
    zero_flags: dict[str, bool],
    comments: dict[str, dict] | None = None,
) -> None:
    """
    Upsert draft rows (overwrite previous draft for same key).
    Drafts are NOT append-only — each Save replaces the previous one.
    """
    now = datetime.now(timezone.utc)

    # Delete existing draft for this key
    _run(
        """
        DELETE FROM gli.volumes.drafts
        WHERE week_id = ? AND site = ? AND product_line = ?
          AND submission_type = ? AND user_id = ?
        """,
        [week_id, site, product_line, submission_type, user_id],
    )

    # Insert fresh rows
    for channel, value in values.items():
        comment_data = (comments or {}).get(channel, {})
        presets = ",".join(comment_data.get("presets", []))
        others  = comment_data.get("others", "") or ""
        _run(
            """
            INSERT INTO gli.volumes.drafts
              (draft_id, saved_at, week_id, site, product_line,
               user_id, submission_type, channel, value_kpcs,
               is_zero_flagged, comment_preset, comment_other)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid.uuid4()), now, week_id, site, product_line,
                user_id, submission_type, channel, value,
                zero_flags.get(channel, False),
                presets, others,
            ],
        )


def delete_draft(week_id: int, site: str, product_line: str,
                 submission_type: str, user_id: str) -> None:
    """Remove draft after a successful Submit."""
    _run(
        """
        DELETE FROM gli.volumes.drafts
        WHERE week_id = ? AND site = ? AND product_line = ?
          AND submission_type = ? AND user_id = ?
        """,
        [week_id, site, product_line, submission_type, user_id],
    )


# ── gli_extract (read-only view for MatteM / Excel Dashboard) ─────────────────

def get_gli_extract(week_id: int) -> pd.DataFrame:
    """Return the full extract for a given week (all sites, both PLs)."""
    return _exec(
        """
        SELECT week_id, site, product_line, submission_type,
               channel, value_kpcs, comment_preset, comment_other
        FROM gli.volumes.gli_extract
        WHERE week_id = ?
        ORDER BY site, product_line, submission_type, channel
        """,
        [week_id],
    )
