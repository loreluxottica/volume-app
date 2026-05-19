# data/db.py
# ─────────────────────────────────────────────────────────────────────────────
# Database layer: reads from and writes to Delta Lake tables via the
# Databricks SQL connector.  All SQL is isolated here — no other module
# touches the DB directly.
#
# Unity Catalog location: `sbx-logistics`.`volume-data-entry-app`
#   weeks         — open/closed week management
#   submissions   — user-entered data (append-only, audit trail)
#   drafts        — drafts saved before submit (overwritten on each Save)
#   app_access    — per-user site access ('*' = every site / admin)
# Volume: /Volumes/sbx-logistics/volume-data-entry-app/app_volume
#         — file storage (exports, uploads, ...)
#
# Connection is established lazily on first query and reused across requests.
# Auth is resolved by databricks.sdk.Config from the environment:
#   - locally:  DATABRICKS_HOST + DATABRICKS_TOKEN (a personal access token)
#   - in-app:   DATABRICKS_HOST + DATABRICKS_CLIENT_ID/DATABRICKS_CLIENT_SECRET
#               (OAuth M2M for the app service principal — injected by
#               Databricks Apps; no PAT is provided in that environment)
# DATABRICKS_HTTP_PATH must be set in either case (see app.yaml for the app).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from databricks import sql
from databricks.sdk.core import Config

# ── Unity Catalog location ────────────────────────────────────────────────────
# Catalog and schema names contain hyphens, so they must be backtick-quoted
# in SQL. Table names are built from these constants — never hardcode them.

_CATALOG = "`sbx-logistics`"
_SCHEMA  = "`volume-data-entry-app`"

_T_WEEKS       = f"{_CATALOG}.{_SCHEMA}.weeks"
_T_SUBMISSIONS = f"{_CATALOG}.{_SCHEMA}.submissions"
_T_DRAFTS      = f"{_CATALOG}.{_SCHEMA}.drafts"
_T_ACCESS      = f"{_CATALOG}.{_SCHEMA}.app_access"

# Volume for file storage (exports, uploads). Not used by the SQL layer below.
VOLUME_PATH = "/Volumes/sbx-logistics/volume-data-entry-app/app_volume"

# ── Connection ────────────────────────────────────────────────────────────────

_conn: sql.client.Connection | None = None
_cfg: Config | None = None


def _config() -> Config:
    """Databricks auth config — auto-detects PAT or OAuth M2M from the env."""
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def _server_hostname(cfg: Config) -> str:
    """Bare hostname for the SQL connector — Config.host carries the scheme."""
    return cfg.host.removeprefix("https://").removeprefix("http://").rstrip("/")


def _http_path() -> str:
    """
    SQL Warehouse HTTP path. When DATABRICKS_HTTP_PATH is bound to a
    `sql_warehouse` app resource via `valueFrom`, Databricks Apps inject the
    warehouse *id* (not the full path) — so accept either form.
    """
    raw = os.environ["DATABRICKS_HTTP_PATH"].strip()
    return raw if raw.startswith("/") else f"/sql/1.0/warehouses/{raw}"


def _get_conn() -> sql.client.Connection:
    global _conn
    if _conn is None:
        cfg = _config()
        _conn = sql.connect(
            server_hostname=_server_hostname(cfg),
            http_path=_http_path(),
            credentials_provider=lambda: cfg.authenticate,
        )
    return _conn


def _reset_conn() -> None:
    """Drop the cached connection so the next call reconnects."""
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None


def _exec(query: str, params: list | None = None) -> pd.DataFrame:
    """
    Execute a SELECT and return a DataFrame. Reconnects once on failure: the
    SQL Warehouse auto-stops after idle time, which drops the cached
    connection — a read is safe to retry.
    """
    for attempt in (1, 2):
        try:
            with _get_conn().cursor() as cur:
                cur.execute(query, params or [])
                # description is None until a result-set query has run; for
                # the SELECTs here it is always populated — `or []` keeps the
                # type checker happy and stays safe if it ever is None.
                cols = [d[0] for d in (cur.description or [])]
                return pd.DataFrame(cur.fetchall(), columns=cols)
        except Exception:
            _reset_conn()
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def _run(query: str, params: list | None = None) -> None:
    """
    Execute an INSERT/UPDATE with no return value. On failure the cached
    connection is dropped (so the next call reconnects) but the statement is
    NOT retried automatically, to avoid a double-write.
    """
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
        f"SELECT week_id, year, created_at FROM {_T_WEEKS} "
        "WHERE is_open = TRUE ORDER BY year DESC, week_id DESC LIMIT 1"
    )
    if df.empty:
        raise RuntimeError(f"No open week found in {_T_WEEKS}")
    # Series.to_dict() is typed dict[Hashable, Any]; the columns are all
    # strings, so rebuild with str keys to match the declared return type.
    return {str(k): v for k, v in df.iloc[0].to_dict().items()}


def create_week(week_id: int, year: int) -> None:
    """Insert a new week (called by the Tuesday scheduler job)."""
    _run(
        f"INSERT INTO {_T_WEEKS} (week_id, year, created_at, is_open) "
        "VALUES (?, ?, ?, TRUE)",
        [week_id, year, datetime.now(timezone.utc)],
    )


# ── admins ────────────────────────────────────────────────────────────────────

def get_access() -> dict[str, set[str]]:
    """
    Return {email: {site, ...}} from the `app_access` table — the sites each
    user may edit. The site '*' means every site (admin). Managed with plain
    SQL — never stored in the repo.
    """
    df = _exec(f"SELECT email, site FROM {_T_ACCESS}")
    out: dict[str, set[str]] = {}
    for _, r in df.iterrows():
        email, site = r["email"], r["site"]
        if email is None or email != email:   # skip NULL / NaN
            continue
        out.setdefault(str(email).strip().lower(), set()).add(str(site).strip())
    return out


# ── submissions ───────────────────────────────────────────────────────────────

def get_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    """
    Return all submission rows for a given week/site/product_line.
    Includes is_amendment and official_log flags so the UI can show history.
    """
    return _exec(
        f"""
        SELECT submission_id, timestamp, submission_type, channel,
               value_kpcs, is_zero_flagged, official_log,
               comment_preset, comment_other, is_amendment
        FROM {_T_SUBMISSIONS}
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
        ORDER BY timestamp ASC
        """,
        [week_id, site, product_line],
    )


def get_latest_submissions(week_id: int, site: str, product_line: str) -> pd.DataFrame:
    """
    Return the latest submission per (submission_type, channel) key — the most
    recent row by timestamp. This is what the UI displays.
    """
    return _exec(
        f"""
        SELECT submission_type, channel, value_kpcs,
               is_zero_flagged, comment_preset, comment_other
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY submission_type, channel
                ORDER BY timestamp DESC
            ) AS _rn
            FROM {_T_SUBMISSIONS}
            WHERE week_id = ? AND site = ? AND product_line = ?
        )
        WHERE _rn = 1
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

    # Step 1: un-mark previous official rows (single round-trip)
    _run(
        f"""
        UPDATE {_T_SUBMISSIONS}
        SET official_log = FALSE
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
          AND submission_type = ?
          AND official_log = TRUE
        """,
        [week_id, site, product_line, submission_type],
    )

    # Step 2: batch INSERT — one round-trip for ALL channels
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

    placeholders = ", ".join(
        ["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?, FALSE, NULL)"] * len(rows_to_insert)
    )
    params = [p for row in rows_to_insert for p in row]

    _run(
        f"""
        INSERT INTO {_T_SUBMISSIONS}
          (submission_id, timestamp, week_id, site, product_line,
           user_id, submission_type, channel, value_kpcs,
           is_zero_flagged, official_log,
           comment_preset, comment_other, is_amendment, ref_submission_id)
        VALUES {placeholders}
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
        FROM {_T_DRAFTS}
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
          AND submission_type = ?
          AND user_id = ?
        """,
        [week_id, site, product_line, submission_type, user_id],
    )


def get_drafts(week_id: int, site: str, product_line: str,
               user_id: str) -> pd.DataFrame:
    """
    Return every draft row for a (week, site, product_line, user) in one
    query — one row per (submission_type, channel). Used to load all drafts
    of a slice at once.
    """
    return _exec(
        f"""
        SELECT submission_type, channel, value_kpcs, is_zero_flagged,
               comment_preset, comment_other
        FROM {_T_DRAFTS}
        WHERE week_id = ?
          AND site = ?
          AND product_line = ?
          AND user_id = ?
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

    # Delete existing draft (single round-trip)
    _run(
        f"""
        DELETE FROM {_T_DRAFTS}
        WHERE week_id = ? AND site = ? AND product_line = ?
          AND submission_type = ? AND user_id = ?
        """,
        [week_id, site, product_line, submission_type, user_id],
    )

    # Batch INSERT all channels (single round-trip)
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

    placeholders = ", ".join(
        ["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(rows_to_insert)
    )
    params = [p for row in rows_to_insert for p in row]

    _run(
        f"""
        INSERT INTO {_T_DRAFTS}
          (draft_id, saved_at, week_id, site, product_line,
           user_id, submission_type, channel, value_kpcs,
           is_zero_flagged, comment_preset, comment_other)
        VALUES {placeholders}
        """,
        params,
    )


def delete_draft(week_id: int, site: str, product_line: str,
                 submission_type: str, user_id: str) -> None:
    """Remove draft after a successful Submit."""
    _run(
        f"""
        DELETE FROM {_T_DRAFTS}
        WHERE week_id = ? AND site = ? AND product_line = ?
          AND submission_type = ? AND user_id = ?
        """,
        [week_id, site, product_line, submission_type, user_id],
    )


# ── extract (read-only, for the Excel Dashboard) ──────────────────────────────

def get_gli_extract(week_id: int) -> pd.DataFrame:
    """
    Return the full extract for a given week (all sites, both PLs) — the latest
    value per key, picked by most recent timestamp. One query; used both by the
    Excel Dashboard and by the in-app GLOBAL view.
    """
    return _exec(
        f"""
        SELECT week_id, site, product_line, submission_type,
               channel, value_kpcs, is_zero_flagged,
               comment_preset, comment_other, timestamp, user_id
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY site, product_line, submission_type, channel
                ORDER BY timestamp DESC
            ) AS _rn
            FROM {_T_SUBMISSIONS}
            WHERE week_id = ?
        )
        WHERE _rn = 1
        ORDER BY site, product_line, submission_type, channel
        """,
        [week_id],
    )