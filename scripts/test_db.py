# scripts/test_db.py
# Test database connectivity and write access for the volumes app (Lakebase).

from __future__ import annotations

import os
import uuid
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import db


@dataclass
class Config:
    week_id: int
    site: str
    product_line: str
    user_id: str


def load_config() -> Config:
    """
    Build the test config from the environment.

    Auth: data/db.py fetches a token via databricks.sdk.Config and injects it
    as the PostgreSQL password. Set DATABRICKS_HOST + DATABRICKS_TOKEN (PAT)
    or DATABRICKS_CONFIG_PROFILE to use a CLI-authenticated profile.

    Required:
      DATABRICKS_LAKEBASE_URL  — PostgreSQL URL without password
      TEST_WEEK_ID             — an open week_id to test against
    """
    missing = []

    def require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            missing.append(name)
        return value or ""

    def optional(name: str, default: str) -> str:
        return os.environ.get(name) or default

    _url     = require("DATABRICKS_LAKEBASE_URL")
    week_id  = require("TEST_WEEK_ID")
    site         = optional("TEST_SITE", "test-site")
    product_line = optional("TEST_PRODUCT_LINE", "test-product-line")
    user_id      = optional("TEST_USER_ID", f"test-user-{uuid.uuid4().hex[:8]}")

    if missing:
        raise SystemExit(
            "Missing environment variables: " + ", ".join(missing) + "\n"
            "Required:\n"
            "  DATABRICKS_LAKEBASE_URL — Lakebase PostgreSQL URL (no password)\n"
            "  TEST_WEEK_ID            — an open week_id to test against\n"
            "Auth (db.py resolves token via databricks.sdk.Config):\n"
            "  DATABRICKS_HOST + DATABRICKS_TOKEN (a PAT), or\n"
            "  DATABRICKS_CONFIG_PROFILE to use a CLI-authenticated profile."
        )

    return Config(
        week_id=int(week_id),
        site=site,
        product_line=product_line,
        user_id=user_id,
    )


def main() -> None:
    config = load_config()
    print("Running DB test against Databricks Lakebase")
    print("Target week_id:", config.week_id)
    print("Test site:", config.site)
    print("Test product line:", config.product_line)

    try:
        current_week = db.get_current_week()
        print("Read check: current open week:", current_week)
    except Exception as exc:
        print("Read check failed:", exc)

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
