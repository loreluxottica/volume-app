# scripts/test_db.py
# Test database connectivity and write access for the volumes app.

from __future__ import annotations

import os
import uuid
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow running as `python scripts/test_db.py`: put the repo root (parent of
# this file's folder) on sys.path so the `data` package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import db


@dataclass
class Config:
    http_path: str
    week_id: int
    site: str
    product_line: str
    user_id: str


def load_config() -> Config:
    """
    Build the test config from the environment.

    Auth is NOT read here — data/db.py resolves credentials via
    databricks.sdk.Config. Set either DATABRICKS_HOST + DATABRICKS_TOKEN
    (a PAT), or DATABRICKS_CONFIG_PROFILE to reuse a CLI-authenticated profile.
    """
    missing = []

    def require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            missing.append(name)
        return value or ""

    def optional(name: str, default: str) -> str:
        return os.environ.get(name) or default

    http_path    = require("DATABRICKS_HTTP_PATH")
    week_id      = require("TEST_WEEK_ID")
    site         = optional("TEST_SITE", "test-site")
    product_line = optional("TEST_PRODUCT_LINE", "test-product-line")
    user_id      = optional("TEST_USER_ID", f"test-user-{uuid.uuid4().hex[:8]}")

    if missing:
        raise SystemExit(
            "Missing environment variables: " + ", ".join(missing) + "\n"
            "Required:\n"
            "  DATABRICKS_HTTP_PATH  — SQL Warehouse HTTP path or warehouse id\n"
            "  TEST_WEEK_ID          — an open week_id to test against\n"
            "Auth (db.py resolves it via databricks.sdk.Config):\n"
            "  DATABRICKS_HOST + DATABRICKS_TOKEN (a PAT), or\n"
            "  DATABRICKS_CONFIG_PROFILE to use a CLI-authenticated profile."
        )

    return Config(
        http_path=http_path,
        week_id=int(week_id),
        site=site,
        product_line=product_line,
        user_id=user_id,
    )


def main() -> None:
    config = load_config()
    print("Running DB test against Databricks SQL Warehouse")
    print("Target week_id:", config.week_id)
    print("Test site:", config.site)
    print("Test product line:", config.product_line)

    try:
        current_week = db.get_current_week()
        print("Read check: current open week:", current_week)
    except Exception as exc:
        print("Read check failed:", exc)
        print("This may still be okay if the open week is not the same as TEST_WEEK_ID.")

    print("Writing a draft row to the drafts table...")
    values = {"TEST_CHANNEL": 0.0}
    zero_flags = {"TEST_CHANNEL": False}
    comments = {"TEST_CHANNEL": {"presets": ["test"], "others": "connectivity-check"}}

    try:
        db.save_draft(
            config.week_id,
            config.site,
            config.product_line,
            config.user_id,
            "test",
            values,
            zero_flags,
            comments,
        )
        print("Write succeeded.")
    except Exception as exc:
        print("Write failed:", exc)
        sys.exit(1)

    try:
        draft = db.get_draft(
            config.week_id,
            config.site,
            config.product_line,
            "test",
            config.user_id,
        )
        print("Draft read back:")
        print(draft)
    except Exception as exc:
        print("Read-back failed:", exc)
        sys.exit(1)

    try:
        db.delete_draft(
            config.week_id,
            config.site,
            config.product_line,
            "test",
            config.user_id,
        )
        print("Cleanup: draft deleted.")
    except Exception as exc:
        print("Cleanup failed:", exc)
        sys.exit(1)

    print("DB connectivity and write test completed successfully.")


if __name__ == "__main__":
    main()
