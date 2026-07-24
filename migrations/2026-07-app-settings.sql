-- migrations/2026-07-app-settings.sql
-- Generic global key/value settings, shared by all users. First use: the
-- Landings second-row view toggle (key 'landings_row2' = 'actual'|'logistics_frc'),
-- changeable only by admins. Run on Lakebase before deploying this version.

CREATE TABLE IF NOT EXISTS volume_data_entry.app_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Redundant with the schema's ALTER DEFAULT PRIVILEGES rule, kept explicit so the
-- app's access does not silently depend on who ran the migration.
GRANT SELECT, INSERT, UPDATE, DELETE ON volume_data_entry.app_settings
  TO "4bb083f4-c641-4be4-be26-1a3c8d247255";

-- Optional explicit default (get_setting also falls back to 'actual' when absent).
INSERT INTO volume_data_entry.app_settings (key, value, updated_by)
VALUES ('landings_row2', 'actual', 'migration')
ON CONFLICT (key) DO NOTHING;
