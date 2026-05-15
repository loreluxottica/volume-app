# scripts/test_db.py
# Test database connectivity and write access for the volumes app.

from __future__ import annotations

import os
import uuid
import sys
from dataclasses import dataclass

from data import db


@dataclass
class Config:
    host: str
    http_path: str
    token: str
    week_id: int
    site: str
    product_line: str
    user_id: str


def load_config() -> Config:
    missing = []
    def get_env(name: str, default: str | None = None) -> str:
        value = os.environ.get(name, default)
        if value is None:
            missing.append(name)
        return value

    host = get_env("DATABRICKS_HOST")
    http_path = get_env("DATABRICKS_HTTP_PATH")
    token = get_env("DATABRICKS_TOKEN")
    week_id = get_env("TEST_WEEK_ID")
    site = get_env("TEST_SITE", "test-site")
    product_line = get_env("TEST_PRODUCT_LINE", "test-product-line")
    user_id = get_env("TEST_USER_ID", f"test-user-{uuid.uuid4().hex[:8]}")

    if missing:
        raise SystemExit(
            "Missing environment variables: " + ", ".join(missing) +
            ".\nPlease set DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN, and optionally TEST_WEEK_ID, TEST_SITE, TEST_PRODUCT_LINE."
        )

    if week_id is None:
        raise SystemExit(
            "Missing environment variable TEST_WEEK_ID. "
            "Set TEST_WEEK_ID to the open week_id you want to test against."
        )

    return Config(
        host=host,
        http_path=http_path,
        token=token,
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
