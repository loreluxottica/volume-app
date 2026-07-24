-- migrations/2026-07-landings-entries.sql
-- Month/Quarter recap values of the Landings page, shared by all users
-- (upsert, last write wins on the natural key).
--
-- ALREADY APPLIED on Lakebase — the table was created by hand before this file
-- existed. Recorded here so a fresh environment can be rebuilt from migrations
-- alone; it is idempotent and a no-op against the current production schema.
--
-- The PK column order must match the ON CONFLICT clause of
-- data/db.py::save_landings_entries exactly, or every Save fails.
-- row_type carries all three values of schema.LANDINGS_ALL_ROW_TYPES: the
-- admin-controlled 2nd-row toggle keeps 'actual' and 'logistics_frc' side by
-- side for the same period, so switching the view never loses the other's data.

CREATE TABLE IF NOT EXISTS volume_data_entry.landings_entries (
  period_type TEXT NOT NULL CHECK (period_type IN ('month','quarter')),
  period_key  TEXT NOT NULL,   -- '2026-06' | '2026-Q2'
  row_type    TEXT NOT NULL,   -- 'business_frc' | 'actual' | 'logistics_frc'
  col_group   TEXT NOT NULL,   -- 'SEDICO' | 'NA' | 'LHKS' | 'SUMARE'
  metric      TEXT NOT NULL,   -- 'py' | 'cy' | 'py_emea' | 'cy_emea'
  value_kpcs  DOUBLE PRECISION,
  updated_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (period_key, row_type, col_group, metric)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON volume_data_entry.landings_entries
  TO "4bb083f4-c641-4be4-be26-1a3c8d247255";
