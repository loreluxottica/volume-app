# gunicorn config for Databricks Apps.
# Databricks injects the port to listen on via DATABRICKS_APP_PORT (default 8000)
# and requires the app to bind 0.0.0.0. app.yaml does not expand env vars, so the
# bind address is resolved here at runtime instead.
import os

bind = f"0.0.0.0:{os.environ.get('DATABRICKS_APP_PORT', '8000')}"
workers = 2
timeout = 120