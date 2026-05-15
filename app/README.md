# Volumes Data Entry Tool — Databricks App

## Project structure

```
volumes_app/
├── app.py                      # Entry point — Dash app, layout, all callbacks
├── requirements.txt            # Python dependencies
├── assets/
│   └── style.css               # Design system (CSS variables, all component styles)
├── components/
│   ├── header.py               # Topbar + app-header (site selector, PL tabs, week badge)
│   └── data_table.py           # Summary bar, main table, Friday panel, legend
└── data/
    ├── schema.py               # Column definitions, NA matrix, deadline schedule
    └── db.py                   # Delta Lake read/write (databricks-sql-connector)
```

## Local development

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:8050
```

The DB layer (`data/db.py`) requires `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`,
and `DATABRICKS_TOKEN` env vars. For local dev without a live warehouse, stub
out `db.py` functions to return empty DataFrames.

## Deploy on Databricks Apps

1. Upload this folder to your Databricks workspace (Repos or DBFS).
2. In the Databricks Apps UI, create a new app and point it to this folder.
3. Set the entry point to `app.py`.
4. Set the following environment variables (Databricks Apps injects these automatically
   when running inside a workspace — no manual setup needed for `HOST` and `TOKEN`):
   - `DATABRICKS_HTTP_PATH`: the HTTP path of your SQL Warehouse
     (e.g. `/sql/1.0/warehouses/abc123def456`)
5. Deploy. The app will be accessible via the Databricks Apps URL.

## Delta Lake schema expected

Tables must exist in Unity Catalog under `gli.volumes.*`:

```sql
-- gli.volumes.weeks
CREATE TABLE gli.volumes.weeks (
  week_id    INT,
  year       INT,
  created_at TIMESTAMP,
  is_open    BOOLEAN
);

-- gli.volumes.submissions (append-only — never UPDATE or DELETE)
CREATE TABLE gli.volumes.submissions (
  submission_id     STRING,
  timestamp         TIMESTAMP,
  week_id           INT,
  site              STRING,
  product_line      STRING,
  user_id           STRING,
  submission_type   STRING,
  channel           STRING,
  value_kpcs        DOUBLE,
  is_zero_flagged   BOOLEAN,
  official_log      BOOLEAN,
  comment_preset    STRING,   -- comma-separated list of preset IDs
  comment_other     STRING,
  is_amendment      BOOLEAN,
  ref_submission_id STRING
);

-- gli.volumes.drafts (overwritten on each Save — NOT append-only)
CREATE TABLE gli.volumes.drafts (
  draft_id          STRING,
  saved_at          TIMESTAMP,
  week_id           INT,
  site              STRING,
  product_line      STRING,
  user_id           STRING,
  submission_type   STRING,
  channel           STRING,
  value_kpcs        DOUBLE,
  is_zero_flagged   BOOLEAN,
  comment_preset    STRING,
  comment_other     STRING
);

-- gli.volumes.gli_extract (SQL view — latest value per key)
CREATE VIEW gli.volumes.gli_extract AS
SELECT
  week_id, site, product_line, submission_type,
  channel, value_kpcs, comment_preset, comment_other
FROM gli.volumes.submissions
WHERE official_log = TRUE;
```

## Connecting the Excel Dashboard

In Excel, use **Data > Get Data > From Database > From SQL Server Database** and
point it at the Databricks SQL Warehouse JDBC/ODBC endpoint. Query
`gli.volumes.gli_extract` filtered by week. Refresh on demand to populate the
weekly report, then export to PPTX/PDF.

## Open items before production

See BBP §8 (open items) for the full list. Critical before go-live:
- Confirm N/A matrix per site (schema.py `NA_FRAMES` / `NA_WEARABLES`)
- Confirm deadline schedule per site (schema.py `DEADLINES`)
- Connect `db.py` calls in `app.py` callbacks (marked with `# In production:`)
- Map Databricks AAD user identity to site (`OWN_SITE` in `app.py`)
- Confirm drafts table architecture (BBP open item #10)
