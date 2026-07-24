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
# Weeks are keyed by (year, week_id): ISO week numbers repeat every year, so
# every submissions/drafts read and write must filter on both columns
# (see migrations/2026-07-add-year.sql).
#
# Connection is established lazily on first query and reused across requests.
# Auth: Databricks Apps injects M2M OAuth automatically; Config().token gives
# the current token, which is used as the PostgreSQL password.
#
# Required env vars:
#   DATABRICKS_LAKEBASE_URL — base PostgreSQL URL without password, e.g.:
#     postgresql://token@ep-xxx.database.westeurope.azuredatabricks.net/databricks_postgres?sslmode=require
#   LAKEBASE_SCHEMA         — schema holding the tables (default: "volume_data_entry")
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse, quote_plus

import pandas as pd
import psycopg2
from databricks.sdk.core import Config

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
def _T_LANDINGS()    -> str: return f"{_pfx()}.landings_entries"
def _T_CHART_WEEKLY() -> str: return f"{_pfx()}.chart_weekly"
def _T_SETTINGS()    -> str: return f"{_pfx()}.app_settings"


VOLUME_PATH = "/Volumes/sbx-logistics/volume-data-entry-app/app_volume"

# ── Connection ────────────────────────────────────────────────────────────────

_conn: psycopg2.extensions.connection | None = None
_cfg: Config | None = None


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def _get_token() -> str:
    """Return the current OAuth/PAT token regardless of auth method."""
    cfg = _config()
    if cfg.token:
        return cfg.token
    # Assign before calling to avoid property-vs-method ambiguity across SDK versions.
    header_factory = cfg.authenticate
    headers = header_factory()
    return headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _build_conn_url() -> str:
    """Build the PostgreSQL connection URL with credentials injected at runtime."""
    try:
        base     = os.environ["DATABRICKS_LAKEBASE_URL"].strip()
        cfg      = _config()
        token    = _get_token()
        parsed   = urlparse(base)
        username = cfg.client_id or parsed.username or "token"
        netloc   = f"{username}:{quote_plus(token)}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception as exc:
        print(f"[error] _build_conn_url failed: {exc}")
        raise


def _get_conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None:
        _conn = psycopg2.connect(_build_conn_url())
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
    """Execute a SELECT; reconnects once on stale-connection failure.
    Only connection-level errors are retried — a genuine SQL error (syntax,
    missing column) surfaces immediately instead of running twice."""
    for attempt in (1, 2):
        try:
            with _get_conn().cursor() as cur:
                cur.execute(query, params or [])
                cols = [d[0] for d in (cur.description or [])]
                return pd.DataFrame(cur.fetchall(), columns=cols)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _reset_conn()
            if attempt == 2:
                raise
        except Exception:
            _reset_conn()
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


def _run_txn(statements: list[tuple[str, list | None]]) -> None:
    """Execute several write statements in ONE transaction — all or nothing.
    The connection runs with autocommit=True, so an explicit BEGIN/COMMIT pair
    opens and closes the transaction. Not retried (same double-write rationale
    as _run); on any failure the whole batch is rolled back."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            for query, params in statements:
                cur.execute(query, params or [])
            cur.execute("COMMIT")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
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


def list_weeks() -> pd.DataFrame:
    """Return every week (open + past), newest first — powers the back-selector."""
    return _exec(
        f"SELECT week_id, year, is_open FROM {_T_WEEKS()} "
        "ORDER BY year DESC, week_id DESC"
    )


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

def get_submissions(week_id: int, year: int, site: str, product_line: str) -> pd.DataFrame:
    """Return all submission rows for a given week/site/product_line."""
    return _exec(
        f"""
        SELECT submission_id, timestamp, submission_type, channel,
               value_kpcs, is_zero_flagged, official_log,
               comment_preset, comment_other, is_amendment
        FROM {_T_SUBMISSIONS()}
        WHERE week_id = %s
          AND year = %s
          AND site = %s
          AND product_line = %s
        ORDER BY timestamp ASC
        """,
        [week_id, year, site, product_line],
    )


def get_latest_submissions(week_id: int, year: int, site: str, product_line: str) -> pd.DataFrame:
    """Return the latest submission per (submission_type, channel) key."""
    return _exec(
        f"""
        WITH ranked AS (
            SELECT submission_type, channel, value_kpcs,
                   is_zero_flagged, comment_preset, comment_other,
                   ROW_NUMBER() OVER (
                       PARTITION BY submission_type, channel
                       ORDER BY timestamp DESC, submission_id DESC
                   ) AS rn
            FROM {_T_SUBMISSIONS()}
            WHERE week_id = %s AND year = %s AND site = %s AND product_line = %s
              AND official_log = TRUE
        )
        SELECT submission_type, channel, value_kpcs,
               is_zero_flagged, comment_preset, comment_other
        FROM ranked
        WHERE rn = 1
        """,
        [week_id, year, site, product_line],
    )


def submit_row(
    week_id: int,
    year: int,
    site: str,
    product_line: str,
    user_id: str,
    submission_type: str,
    values: dict[str, float | None],
    zero_flags: dict[str, bool],
    comments: dict[str, dict] | None = None,
    is_delay: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    delay_ts = now if is_delay else None

    rows_to_insert = []
    for channel, value in values.items():
        if value is None and not zero_flags.get(channel):
            continue
        comment_data = (comments or {}).get(channel, {})
        presets = ",".join(comment_data.get("presets", []))
        others = comment_data.get("others", "") or ""
        rows_to_insert.append((
            str(uuid.uuid4()), now, week_id, year, site, product_line,
            user_id, submission_type, channel, value,
            zero_flags.get(channel, False),
            presets, others,
        ))

    if not rows_to_insert:
        return

    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(rows_to_insert))
    params = [p for row in rows_to_insert for p in row]

    merge_sql = f"""
        MERGE INTO {_T_SUBMISSIONS()} AS t
        USING (
            SELECT * FROM (
                VALUES {placeholders}
            ) AS s(
                submission_id, timestamp, week_id, year, site, product_line,
                user_id, submission_type, channel, value_kpcs,
                is_zero_flagged, comment_preset, comment_other
            )
        ) AS src
        ON t.week_id = src.week_id
           AND t.year = src.year
           AND t.site = src.site
           AND t.product_line = src.product_line
           AND t.submission_type = src.submission_type
           AND t.channel = src.channel
           AND t.official_log = TRUE
        WHEN MATCHED THEN
            UPDATE SET official_log = FALSE
        """

    placeholders_insert = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, FALSE, NULL, %s, %s)"] * len(rows_to_insert))
    # Separate param list: append (is_delay, delay_timestamp) to each row so the
    # two new columns don't corrupt the 13-col MERGE params reused above.
    insert_params = [p for row in rows_to_insert for p in (*row, is_delay, delay_ts)]
    insert_sql = f"""
        INSERT INTO {_T_SUBMISSIONS()}
          (submission_id, timestamp, week_id, year, site, product_line,
           user_id, submission_type, channel, value_kpcs,
           is_zero_flagged, official_log,
           comment_preset, comment_other, is_amendment, ref_submission_id,
           is_delay, delay_timestamp)
        VALUES {placeholders_insert}
        """

    # One transaction: never demote the old official rows without landing the
    # new ones (a connection drop between the two would lose the channel).
    _run_txn([(merge_sql, params), (insert_sql, insert_params)])


# ── drafts ────────────────────────────────────────────────────────────────────

def get_draft(week_id: int, year: int, site: str, product_line: str,
              submission_type: str, user_id: str) -> pd.DataFrame:
    """Return draft rows for a given key (one row per channel)."""
    return _exec(
        f"""
        SELECT channel, value_kpcs, is_zero_flagged, comment_preset, comment_other
        FROM {_T_DRAFTS()}
        WHERE week_id = %s
          AND year = %s
          AND site = %s
          AND product_line = %s
          AND submission_type = %s
          AND user_id = %s
        """,
        [week_id, year, site, product_line, submission_type, user_id],
    )


def get_drafts(week_id: int, year: int, site: str, product_line: str,
               user_id: str) -> pd.DataFrame:
    """Return every draft row for a (week, site, product_line, user) in one query."""
    return _exec(
        f"""
        SELECT submission_type, channel, value_kpcs, is_zero_flagged,
               comment_preset, comment_other
        FROM {_T_DRAFTS()}
        WHERE week_id = %s
          AND year = %s
          AND site = %s
          AND product_line = %s
          AND user_id = %s
        """,
        [week_id, year, site, product_line, user_id],
    )


def save_draft(
    week_id: int,
    year: int,
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
            str(uuid.uuid4()), now, week_id, year, site, product_line,
            user_id, submission_type, channel, value,
            zero_flags.get(channel, False),
            presets, others,
        ))

    if not rows_to_insert:
        return

    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(rows_to_insert))
    params = [p for row in rows_to_insert for p in row]

    _run(
        f"""
        MERGE INTO {_T_DRAFTS()} AS t
        USING (
            SELECT * FROM (
                VALUES {placeholders}
            ) AS s(
                draft_id, saved_at, week_id, year, site, product_line,
                user_id, submission_type, channel, value_kpcs,
                is_zero_flagged, comment_preset, comment_other
            )
        ) AS src
        ON t.week_id = src.week_id
           AND t.year = src.year
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
            INSERT (draft_id, saved_at, week_id, year, site, product_line, user_id, submission_type, channel, value_kpcs, is_zero_flagged, comment_preset, comment_other)
            VALUES (src.draft_id, src.saved_at, src.week_id, src.year, src.site, src.product_line, src.user_id, src.submission_type, src.channel, src.value_kpcs, src.is_zero_flagged, src.comment_preset, src.comment_other)
        """,
        params,
    )


def delete_draft(week_id: int, year: int, site: str, product_line: str,
                 submission_type: str, user_id: str) -> None:
    """Remove draft after a successful Submit."""
    _run(
        f"""
        DELETE FROM {_T_DRAFTS()}
        WHERE week_id = %s AND year = %s AND site = %s AND product_line = %s
          AND submission_type = %s AND user_id = %s
        """,
        [week_id, year, site, product_line, submission_type, user_id],
    )


# ── landings (recap page) ─────────────────────────────────────────────────────

def get_landings_entries(period_key: str) -> pd.DataFrame:
    """Return saved Month/Quarter recap values for a period ('2026-06' / '2026-Q2')."""
    return _exec(
        f"""
        SELECT row_type, col_group, metric, value_kpcs
        FROM {_T_LANDINGS()}
        WHERE period_key = %s
        """,
        [period_key],
    )


def save_landings_entries(
    period_type: str,
    period_key: str,
    user_id: str,
    entries: list[tuple[str, str, str, float | None]],
) -> None:
    """Upsert recap values — entries = [(row_type, col_group, metric, value), ...].
    Shared across users: last write wins on the natural key."""
    if not entries:
        return
    now = datetime.now(timezone.utc)
    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(entries))
    params = []
    for row_type, col_group, metric, value in entries:
        params.extend([period_type, period_key, row_type, col_group, metric,
                       value, user_id, now])
    _run(
        f"""
        INSERT INTO {_T_LANDINGS()}
          (period_type, period_key, row_type, col_group, metric,
           value_kpcs, updated_by, updated_at)
        VALUES {placeholders}
        ON CONFLICT (period_key, row_type, col_group, metric)
        DO UPDATE SET
            value_kpcs = EXCLUDED.value_kpcs,
            updated_by = EXCLUDED.updated_by,
            updated_at = EXCLUDED.updated_at
        """,
        params,
    )


def get_landings_weekly(year: int) -> pd.DataFrame:
    """Authoritative FRAMES whls_net / whls_net_ow_emea rows for every week of a
    year (chart + WK block of the Landings page). Same ROW_NUMBER dedup as
    get_gli_extract.

    Scoped on submissions.year, NOT only on the weeks subquery: ISO week numbers
    repeat every year, so `week_id IN (weeks of 2026)` alone also matches the 2025
    rows carrying the same week numbers and sums two years into one chart. The
    weeks subquery stays as the second filter — it restricts to weeks that were
    actually opened."""
    return _exec(
        f"""
        WITH ranked AS (
            SELECT week_id, site, submission_type, channel, value_kpcs,
                   ROW_NUMBER() OVER (
                       PARTITION BY week_id, site, submission_type, channel
                       ORDER BY timestamp DESC
                   ) AS rn
            FROM {_T_SUBMISSIONS()}
            WHERE official_log = TRUE
              AND year = %s
              AND product_line = 'FRAMES'
              AND channel IN ('whls_net', 'whls_net_ow_emea', 'ds_na')
              AND submission_type IN ('py', 'mon_frc', 'fri_frc', 'actual')
              AND week_id IN (SELECT week_id FROM {_T_WEEKS()} WHERE year = %s)
        )
        SELECT week_id, site, submission_type, channel, value_kpcs
        FROM ranked
        WHERE rn = 1
        """,
        [year, year],
    )


# ── chart weekly series (dedicated table, one row per year+week) ──────────────

def get_chart_weekly(year: int) -> pd.DataFrame:
    """Weekly Ship series for a data year: historical + optional manual per week.
    Plotted value = COALESCE(value_manual, value_hist) — resolved by the caller.
    Read for year-1 (blue PY line) and for the current year (fallback for the red
    CY line on weeks that predate go-live, where no Actual was ever submitted)."""
    return _exec(
        f"""
        SELECT week, value_hist, value_manual
        FROM {_T_CHART_WEEKLY()}
        WHERE year = %s
        ORDER BY week
        """,
        [year],
    )


def save_chart_weekly_manual(year: int, week: int, value: float | None,
                             user_id: str) -> None:
    """Upsert ONLY the manual override for a week — never touches value_hist."""
    _run(
        f"""
        INSERT INTO {_T_CHART_WEEKLY()} (year, week, value_manual, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (year, week)
        DO UPDATE SET value_manual = EXCLUDED.value_manual,
                      updated_by   = EXCLUDED.updated_by,
                      updated_at   = EXCLUDED.updated_at
        """,
        [year, week, value, user_id, datetime.now(timezone.utc)],
    )


# ── app settings (generic global key/value) ───────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    """Return a global setting value, or `default` when the key is absent."""
    df = _exec(f"SELECT value FROM {_T_SETTINGS()} WHERE key = %s", [key])
    if df.empty or df.iloc[0]["value"] is None:
        return default
    return str(df.iloc[0]["value"])


def set_setting(key: str, value: str, user_id: str) -> None:
    """Upsert a global setting — last write wins on the key."""
    _run(
        f"""
        INSERT INTO {_T_SETTINGS()} (key, value, updated_by, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (key)
        DO UPDATE SET value = EXCLUDED.value,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = EXCLUDED.updated_at
        """,
        [key, value, user_id, datetime.now(timezone.utc)],
    )


# ── extract (read-only, for the Excel Dashboard) ──────────────────────────────

def get_gli_extract(week_id: int, year: int) -> pd.DataFrame:
    """Return the full extract for a given week — the authoritative row per key."""
    return _exec(
        f"""
        WITH ranked AS (
            SELECT week_id, year, site, product_line, submission_type,
                   channel, value_kpcs, is_zero_flagged,
                   comment_preset, comment_other, timestamp, user_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY site, product_line, submission_type, channel
                       ORDER BY timestamp DESC, submission_id DESC
                   ) AS rn
            FROM {_T_SUBMISSIONS()}
            WHERE week_id = %s AND year = %s AND official_log = TRUE
        )
        SELECT week_id, year, site, product_line, submission_type,
               channel, value_kpcs, is_zero_flagged,
               comment_preset, comment_other, timestamp, user_id
        FROM ranked
        WHERE rn = 1
        ORDER BY site, product_line, submission_type, channel
        """,
        [week_id, year],
    )


# ── local SQLite swap (dev only) ──────────────────────────────────────────────
# When VOLUMES_LOCAL_DB is set, every public read/write above is rebound to the
# offline SQLite backend in data/local_db.py (seeded local_volumes.db). Import is
# lazy and gated on the env var, so production/Lakebase never touches the module
# (which is gitignored). Keep this list in sync with local_db.py's public API.
if os.environ.get("VOLUMES_LOCAL_DB"):
    from data import local_db as _local
    for _name in (
        "get_current_week", "list_weeks", "create_week", "get_access",
        "get_submissions", "get_latest_submissions", "submit_row",
        "get_draft", "get_drafts", "save_draft", "delete_draft",
        "get_landings_entries", "save_landings_entries",
        "get_landings_weekly", "get_chart_weekly", "save_chart_weekly_manual",
        "get_setting", "set_setting",
        "get_gli_extract",
    ):
        globals()[_name] = getattr(_local, _name)
    print("[db] VOLUMES_LOCAL_DB set — using local SQLite backend")
