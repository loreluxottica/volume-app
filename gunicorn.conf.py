# gunicorn config for Databricks Apps.
# Databricks injects the port to listen on via DATABRICKS_APP_PORT (default 8000)
# and requires the app to bind 0.0.0.0. app.yaml does not expand env vars, so the
# bind address is resolved here at runtime instead.
import os

bind = f"0.0.0.0:{os.environ.get('DATABRICKS_APP_PORT', '8000')}"
# Single worker on purpose: Dash callbacks here return whole dcc.Store
# snapshots, so concurrent workers can race and overwrite each other's
# updates (lost cell values, spurious tab switches). Load is tiny — one
# worker is enough.
workers = 1
timeout = 120