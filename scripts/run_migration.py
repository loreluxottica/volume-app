#!/usr/bin/env python
# scripts/run_migration.py
# ─────────────────────────────────────────────────────────────────────────────
# Run a .sql migration (or an ad-hoc query with --sql) against Lakebase.
#
# Lakebase is plain PostgreSQL, but the password is a Databricks OAuth token that
# rotates (~1h) — there is no static connection string to keep in a client. This
# builds the URL at call time from the local Databricks CLI credentials, the same
# recipe as data/db.py::_build_conn_url.
#
# Two deliberate differences from the app's connection:
#   * the role comes from LAKEBASE_ROLE, not from the URL. data/db.py resolves the
#     user as `cfg.client_id or parsed.username`; under CLI (u2m) OAuth client_id
#     is None, so it would fall back to the URL's username — the APP's service
#     principal — and try to authenticate that role with a personal token.
#   * autocommit is OFF: one transaction per file, so a failure halfway leaves
#     nothing behind. Postgres DDL is transactional; use it.
#
# Usage (PowerShell):
#   $env:DATABRICKS_LAKEBASE_URL = "postgresql://token@ep-….database.windows.net/databricks_postgres?sslmode=require"
#   $env:LAKEBASE_ROLE = "name.surname@luxottica.com"
#   .\.venv\Scripts\python.exe .\scripts\run_migration.py .\migrations\2026-07-landings-entries.sql
#   .\.venv\Scripts\python.exe .\scripts\run_migration.py --sql "SELECT 1"
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse, urlunparse

import psycopg2
from databricks.sdk.core import Config

DEFAULT_URL = (
    "postgresql://token@ep-empty-mode-e2j49wxy.database.westeurope.azuredatabricks.net"
    "/databricks_postgres?sslmode=require"
)


def _token(cfg: Config) -> str:
    if cfg.token:
        return cfg.token
    header_factory = cfg.authenticate
    headers = header_factory()
    return headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _conn_url(role: str, profile: str | None) -> str:
    base = os.environ.get("DATABRICKS_LAKEBASE_URL", DEFAULT_URL).strip()
    # ~/.databrickscfg holds several profiles pointing at the same host, which
    # makes `databricks auth token` ambiguous — always pin one.
    cfg = Config(profile=profile) if profile else Config()
    parsed = urlparse(base)
    netloc = f"{quote_plus(role)}:{quote_plus(_token(cfg))}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _print_result(cur) -> None:
    """Print a SELECT result as an aligned table; no-op for statements."""
    if cur.description is None:
        return
    cols = [d[0] for d in cur.description]
    rows = [["" if v is None else str(v) for v in r] for r in cur.fetchall()]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
              for i, c in enumerate(cols)]
    line = "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(cols))))
    print(f"({len(rows)} rows)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run SQL against Lakebase.")
    ap.add_argument("path", nargs="?", help="path to a .sql file")
    ap.add_argument("--sql", help="inline SQL instead of a file")
    ap.add_argument("--role", help="postgres role (default: $LAKEBASE_ROLE)")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"),
                    help="~/.databrickscfg profile (default: $DATABRICKS_CONFIG_PROFILE)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the SQL and exit without connecting")
    args = ap.parse_args()

    if bool(args.path) == bool(args.sql):
        ap.error("give exactly one of: a .sql path, or --sql")

    if args.sql:
        sql, label = args.sql, "<inline>"
    else:
        p = Path(args.path)
        if not p.is_file():
            print(f"[error] no such file: {p}", file=sys.stderr)
            return 2
        sql, label = p.read_text(encoding="utf-8"), str(p)

    if args.dry_run:
        print(sql)
        return 0

    role = args.role or os.environ.get("LAKEBASE_ROLE")
    if not role:
        print("[error] set LAKEBASE_ROLE (your Databricks email) or pass --role",
              file=sys.stderr)
        return 2

    conn = psycopg2.connect(_conn_url(role, args.profile))
    conn.autocommit = False          # one transaction per file
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            _print_result(cur)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"[FAIL] {label}\n{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"[OK]   {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
