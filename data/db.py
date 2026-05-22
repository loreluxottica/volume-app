# data/db.py
# ─────────────────────────────────────────────────────────────────────────────
# Database layer: reads from and writes to tables via the Lakebase PostgreSQL
# endpoint. All SQL is isolated here — no other module touches the DB directly.
#
# Lakebase database: databricks_postgres, schema: volume_data_entry
#   weeks         — open/closed week management
#   submissions   — user-entered data (append-only, audit trail)
#   drafts        — drafts saved before submit (overwritten on each Save)
#   app_access    — per-user site access ('*' = every site / admin)
#
# Connection is established lazily on first query and reused across requests.
# Auth: Databricks Apps injects a full PostgreSQL connection URL (with
# credentials) into DATABASE_URL via the "database" lakebase resource.
#
# Required env vars (set by app.yaml resource binding):
#   DATABASE_URL    — full PostgreSQL URL injected by the Lakebase resource
#   LAKEBASE_SCHEMA — schema holding the tables (default: "volume_data_entry")
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import psycopg2

# ── Table names ───────────────────────────────────────────────────────────────

_table_prefix: str | None = None


def _pfx() -> str:
    global _table_prefix
    if _table_prefix is None:
        raw = os.environ.get("LAKEBASE_SCHEMA", "volume_data_entry").strip()
        _table_prefix = f'"{raw}"'
    return _table_prefix


def _T_WEEKS()       -> str: return f"{_pfx()}.weeks"
def _T_SUBMISSIONS() -> str: return f"{_pfx()}.submissions"
def _T_DRAFTS()      -> str: return f"{_pfx()}.drafts"
def _T_ACCESS()      -> str: return f"{_pfx()}.app_access"


VOLUME_PATH = "/Volumes/sbx-logistics/volume-data-entry-app/app_volume"

# ── Connection ────────────────────────────────────────────────────────────────

_conn: psycopg2.extensions.connection | None = None


def _get_conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None:
        _conn = psycopg2.connect(os.environ["DATABASE_URL"])
        _conn.autocommit = True
    return _conn


def _reset_conn() -> None:
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None


def _exec(query: str, params: list | None = None) -> pd.DataFrame:
    """Execute a SELECT; reconnects once on stale-connection failure."""
    for attempt in (1, 2):
        try:
            with _get_conn().cursor() as cur:
                cur.execute(query, params or [])
                cols = [d[0] for d in (cur.description or [])]
                return pd.DataFrame(cur.fetchall(), columns=cols)
        except Exception:
            _reset_conn()
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def _run(query: str, params: list | None = None) -> None:
    """Execute an INSERT/UPDATE/MERGE; not retried to avoid double-writes."""
    try:
        with _get_conn().cursor() as cur:
            cur.execute(query, params or [])
    except Exception:
        _reset_conn()
        raise


# ── weeks ─────────────────────────────────────────────────────────────────────

def get_current_week() -> dict[str, Any]:
    """Return the most recent open week record."""
    df = _exec(
        f"SELECT week_id, year, created_at FROM {_T_WEEKS()} "
        "WHERE is_open = TRUE ORDER BY year DESC, week_id DESC LIMIT 1"
    )
    if df.empty:
        raise RuntimeError(f"No open week found in {_T_WEEKS()}")
    return {str(k): v for k, v in df.iloc[0].to_dict().items()}


def create_week(week_id: int, year: int) -> None:
    """Insert a new week (called by the Tuesday scheduler job)."""
    _run(
        f"INSERT INTO {_T_WEEKS()} (week_id, year, created_at, is_open) "
        "VALUES (%s, %s, %s, TRUE)",
        [week_id, year, datetime.now(timezone.utc)],
    )


# ── admins ────────────────────────────────────────────────────────────────────

def get_access() -> dict[str, set[str]]:
    """Return {email: {site, ...}} from the app_access table."""
    df = _exec(f"SELECT email, site FROM {_T_ACCESS()}")
    out: dict[str, set[str]] = {}
    for _, r in df.iterrows():
        email, site = r["email"], r["site"]
        if email is None or email != email:
            continue
        out.setdefault(str(email).strip().lower(), set()).add(str(site).strip())
    return out


# ── submissions ───────────────────────────────────────────────────────────────

def get_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    """Return all submission rows for a given week/site/product_line."""
    return _exec(
        f"""
        SELECT submission_id, timestamp, submission_type, channel,
               value_kpcs, is_zero_flagged, official_log,
               comment_preset, comment_other, is_amendment
        FROM {_T_SUBMISSIONS()}
        WHERE week_id = %s
          AND site = %s
          AND product_line = %s
        ORDER BY timestamp ASC
        """,
        [week_id, site, product_line],
    )


def get_latest_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    """Return the latest submission per (submission_type, channel) key."""
    return _exec(
        f"""
        WITH ranked AS (
            SELECT submission_type, channel, value_kpcs,
                   is_zero_flagged, comment_preset, comment_other,
                   ROW_NUMBER() OVER (
                       PARTITION BY submission_type, channel
                       ORDER BY timestamp DESC
                   ) AS rn
            FROM {_T_SUBMISSIONS()}
            WHERE week_id = %s AND site = %s AND product_line = %s
              AND official_log = TRUE
        )
        SELECT submission_type, channel, value_kpcs,
               is_zero_flagged, comment_preset, comment_other
        FROM ranked
        WHERE rn = 1
        """,
        [week_id, site, product_line],
    )


def submit_row(
    week_id: int,
    site: str,
    product_line: str,
    user_id: str,
    submission_type: str,
    values: dict[str, float | None],
    zero_flags: dict[str, bool],
    comments: dict[str, dict] | None = None,
) -> None:
    now = datetime.now(timezone.utc)

    rows_to_insert = []
    for channel, value in values.items():
        if value is None and not zero_flags.get(channel):
            continue
        comment_data = (comments or {}).get(channel, {})
        presets = ",".join(comment_data.get("presets", []))
        others = comment_data.get("others", "") or ""
        rows_to_insert.append((
            str(uuid.uuid4()), now, week_id, site, product_line,
            user_id, submission_type, channel, value,
            zero_flags.get(channel, False),
            presets, others,
        ))

    if not rows_to_insert:
        return

    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(rows_to_insert))
    params = [p for row in rows_to_insert for p in row]

    _run(
        f"""
        MERGE INTO {_T_SUBMISSIONS()} AS t
        USING (
            SELECT * FROM (
                VALUES {placeholders}
            ) AS s(
                submission_id, timestamp, week_id, site, product_line,
                user_id, submission_type, channel, value_kpcs,
                is_zero_flagged, comment_preset, comment_other
            )
        ) AS src
        ON t.week_id = src.week_id
           AND t.site = src.site
           AND t.product_line = src.product_line
           AND t.submission_type = src.submission_type
           AND t.channel = src.channel
           AND t.official_log = TRUE
        WHEN MATCHED THEN
            UPDATE SET official_log = FALSE
        """,
        params,
    )

    placeholders_insert = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, FALSE, NULL)"] * len(rows_to_insert))
    _run(
        f"""
        INSERT INTO {_T_SUBMISSIONS()}
          (submission_id, timestamp, week_id, site, product_line,
           user_id, submission_type, channel, value_kpcs,
           is_zero_flagged, official_log,
           comment_preset, comment_other, is_amendment, ref_submission_id)
        VALUES {placeholders_insert}
        """,
        params,
    )


# ── drafts ────────────────────────────────────────────────────────────────────

def get_draft(week_id: int, site: str, product_line: str,
              submission_type: str, user_id: str) -> pd.DataFrame:
    """Return draft rows for a given key (one row per channel)."""
    return _exec(
        f"""
        SELECT channel, value_kpcs, is_zero_flagged, comment_preset, comment_other
        FROM {_T_DRAFTS()}
        WHERE week_id = %s
          AND site = %s
          AND product_line = %s
          AND submission_type = %s
          AND user_id = %s
        """,
        [week_id, site, product_line, submission_type, user_id],
    )


def get_drafts(week_id: int, site: str, product_line: str,
               user_id: str) -> pd.DataFrame:
    """Return every draft row for a (week, site, product_line, user) in one query."""
    return _exec(
        f"""
        SELECT submission_type, channel, value_kpcs, is_zero_flagged,
               comment_preset, comment_other
        FROM {_T_DRAFTS()}
        WHERE week_id = %s
          AND site = %s
          AND product_line = %s
          AND user_id = %s
        """,
        [week_id, site, product_line, user_id],
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
    now = datetime.now(timezone.utc)

    rows_to_insert = []
    for channel, value in values.items():
        comment_data = (comments or {}).get(channel, {})
        presets = ",".join(comment_data.get("presets", []))
        others = comment_data.get("others", "") or ""
        rows_to_insert.append((
            str(uuid.uuid4()), now, week_id, site, product_line,
            user_id, submission_type, channel, value,
            zero_flags.get(channel, False),
            presets, others,
        ))

    if not rows_to_insert:
        return

    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(rows_to_insert))
    params = [p for row in rows_to_insert for p in row]

    _run(
        f"""
        MERGE INTO {_T_DRAFTS()} AS t
        USING (
            SELECT * FROM (
                VALUES {placeholders}
            ) AS s(
                draft_id, saved_at, week_id, site, product_line,
                user_id, submission_type, channel, value_kpcs,
                is_zero_flagged, comment_preset, comment_other
            )
        ) AS src
        ON t.week_id = src.week_id
           AND t.site = src.site
           AND t.product_line = src.product_line
           AND t.submission_type = src.submission_type
           AND t.user_id = src.user_id
           AND t.channel = src.channel
        WHEN MATCHED THEN
            UPDATE SET
                saved_at = src.saved_at,
                value_kpcs = src.value_kpcs,
                is_zero_flagged = src.is_zero_flagged,
                comment_preset = src.comment_preset,
                comment_other = src.comment_other
        WHEN NOT MATCHED THEN
            INSERT (draft_id, saved_at, week_id, site, product_line, user_id, submission_type, channel, value_kpcs, is_zero_flagged, comment_preset, comment_other)
            VALUES (src.draft_id, src.saved_at, src.week_id, src.site, src.product_line, src.user_id, src.submission_type, src.channel, src.value_kpcs, src.is_zero_flagged, src.comment_preset, src.comment_other)
        """,
        params,
    )


def delete_draft(week_id: int, site: str, product_line: str,
                 submission_type: str, user_id: str) -> None:
    """Remove draft after a successful Submit."""
    _run(
        f"""
        DELETE FROM {_T_DRAFTS()}
        WHERE week_id = %s AND site = %s AND product_line = %s
          AND submission_type = %s AND user_id = %s
        """,
        [week_id, site, product_line, submission_type, user_id],
    )


# ── extract (read-only, for the Excel Dashboard) ──────────────────────────────

def get_gli_extract(week_id: int) -> pd.DataFrame:
    """Return the full extract for a given week — the authoritative row per key."""
    return _exec(
        f"""
        WITH ranked AS (
            SELECT week_id, site, product_line, submission_type,
                   channel, value_kpcs, is_zero_flagged,
                   comment_preset, comment_other, timestamp, user_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY site, product_line, submission_type, channel
                       ORDER BY timestamp DESC
                   ) AS rn
            FROM {_T_SUBMISSIONS()}
            WHERE week_id = %s AND official_log = TRUE
        )
        SELECT week_id, site, product_line, submission_type,
               channel, value_kpcs, is_zero_flagged,
               comment_preset, comment_other, timestamp, user_id
        FROM ranked
        WHERE rn = 1
        ORDER BY site, product_line, submission_type, channel
        """,
        [week_id],
    )
