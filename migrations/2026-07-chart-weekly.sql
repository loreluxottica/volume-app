-- migrations/2026-07-chart-weekly.sql
-- Weekly Ship series behind the Landings chart, one row per (year, week).
--
--   value_hist   authoritative weekly total (raw pcs) — READ-ONLY, never edited
--                by the app.
--   value_manual optional override. The chart plots COALESCE(value_manual,
--                value_hist), so the historical row is never overwritten.
--
-- The table carries EVERY year, not just the previous one: the app reads year-1
-- for the blue PY line and the current year as the fallback for the red CY line
-- on weeks that predate go-live (weeks with no Actual submissions yet).
-- Submissions always win where they exist.
--
-- Grants: an ALTER DEFAULT PRIVILEGES rule on volume_data_entry already grants
-- the app's service principal arwd on tables created by the schema owner. The
-- explicit GRANT below is belt-and-braces and documents the intent — update the
-- role if production runs under a different service principal.

CREATE TABLE IF NOT EXISTS volume_data_entry.chart_weekly (
  year         INTEGER NOT NULL,
  week         INTEGER NOT NULL,
  value_hist   DOUBLE PRECISION,
  value_manual DOUBLE PRECISION,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (year, week)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON volume_data_entry.chart_weekly
  TO "4bb083f4-c641-4be4-be26-1a3c8d247255";

-- ── 2025 — real weekly Ship, weeks 0..51 (week 52 has no value in source) ─────
-- value_manual stays NULL: no override has been requested for any week.
INSERT INTO volume_data_entry.chart_weekly (year, week, value_hist, updated_by) VALUES
  (2025,  0,  456000, 'seed'),
  (2025,  1,  888244, 'seed'),
  (2025,  2,  943452, 'seed'),
  (2025,  3,  903263, 'seed'),
  (2025,  4,  963678, 'seed'),
  (2025,  5, 1194429, 'seed'),
  (2025,  6, 1128650, 'seed'),
  (2025,  7, 1106138, 'seed'),
  (2025,  8, 1198985, 'seed'),
  (2025,  9, 1443232, 'seed'),
  (2025, 10, 1585428, 'seed'),
  (2025, 11, 1434410, 'seed'),
  (2025, 12, 1563391, 'seed'),
  (2025, 13, 1142509, 'seed'),
  (2025, 14, 1219751, 'seed'),
  (2025, 15, 1085800, 'seed'),
  (2025, 16, 1014833, 'seed'),
  (2025, 17, 1127613, 'seed'),
  (2025, 18, 1142852, 'seed'),
  (2025, 19, 1180195, 'seed'),
  (2025, 20, 1336473, 'seed'),
  (2025, 21, 1259558, 'seed'),
  (2025, 22, 1286066, 'seed'),
  (2025, 23, 1385404, 'seed'),
  (2025, 24, 1333087, 'seed'),
  (2025, 25, 1266624, 'seed'),
  (2025, 26,  668129, 'seed'),
  (2025, 27,  850542, 'seed'),
  (2025, 28, 1062818, 'seed'),
  (2025, 29,  922713, 'seed'),
  (2025, 30,  934976, 'seed'),
  (2025, 31,  915466, 'seed'),
  (2025, 32,  682670, 'seed'),
  (2025, 33,  744812, 'seed'),
  (2025, 34, 1033824, 'seed'),
  (2025, 35,  958532, 'seed'),
  (2025, 36, 1264461, 'seed'),
  (2025, 37, 1226798, 'seed'),
  (2025, 38, 1336427, 'seed'),
  (2025, 39, 1033824, 'seed'),
  (2025, 40, 1030981, 'seed'),
  (2025, 41,  995793, 'seed'),
  (2025, 42, 1102241, 'seed'),
  (2025, 43, 1025217, 'seed'),
  (2025, 44, 1037091, 'seed'),
  (2025, 45, 1099649, 'seed'),
  (2025, 46, 1108156, 'seed'),
  (2025, 47, 1002300, 'seed'),
  (2025, 48, 1064758, 'seed'),
  (2025, 49, 1032628, 'seed'),
  (2025, 50, 1019636, 'seed'),
  (2025, 51,  453363, 'seed')
ON CONFLICT (year, week) DO NOTHING;

-- ── 2026 — real weekly Ship for the weeks that predate go-live, 0..29 ─────────
-- Backfill only: from the first week the plants submit Actuals, the submissions
-- table takes over for that week and the weekly job (see below) appends the
-- closed weeks here so the series stays complete for next year's PY line.
INSERT INTO volume_data_entry.chart_weekly (year, week, value_hist, updated_by) VALUES
  (2026,  0,  172159, 'seed'),
  (2026,  1,  987776, 'seed'),
  (2026,  2,  947521, 'seed'),
  (2026,  3,  886200, 'seed'),
  (2026,  4,  912593, 'seed'),
  (2026,  5, 1153628, 'seed'),
  (2026,  6, 1122887, 'seed'),
  (2026,  7,  919398, 'seed'),
  (2026,  8, 1166640, 'seed'),
  (2026,  9, 1293357, 'seed'),
  (2026, 10, 1608340, 'seed'),
  (2026, 11, 1768499, 'seed'),
  (2026, 12, 1529171, 'seed'),
  (2026, 13, 1150395, 'seed'),
  (2026, 14, 1018138, 'seed'),
  (2026, 15, 1242176, 'seed'),
  (2026, 16, 1097313, 'seed'),
  (2026, 17,  895031, 'seed'),
  (2026, 18, 1263446, 'seed'),
  (2026, 19, 1213616, 'seed'),
  (2026, 20, 1122933, 'seed'),
  (2026, 21, 1205008, 'seed'),
  (2026, 22, 1149315, 'seed'),
  (2026, 23, 1404060, 'seed'),
  (2026, 24, 1418747, 'seed'),
  (2026, 25, 1607794, 'seed'),
  (2026, 26,  930236, 'seed'),
  (2026, 27, 1028412, 'seed'),
  (2026, 28,  125400, 'seed'),
  (2026, 29,  125400, 'seed')
ON CONFLICT (year, week) DO NOTHING;

-- ── Weekly append (scheduled task, run after the Actual deadline) ─────────────
-- Upserts ONLY the just-closed week, and only when submissions actually hold a
-- value for it — the HAVING guard is what stops a week with no submissions from
-- blanking a backfilled row above.
--
-- INSERT INTO volume_data_entry.chart_weekly (year, week, value_hist, updated_by)
-- SELECT year, week_id, SUM(value_kpcs), 'weekly-job'
-- FROM volume_data_entry.submissions
-- WHERE official_log AND product_line = 'FRAMES'
--   AND channel = 'whls_net' AND submission_type = 'actual'
--   AND year = :y AND week_id = :w
-- GROUP BY year, week_id
-- HAVING SUM(value_kpcs) IS NOT NULL
-- ON CONFLICT (year, week) DO UPDATE
--   SET value_hist = EXCLUDED.value_hist,
--       updated_by = EXCLUDED.updated_by,
--       updated_at = now();
--
-- ── Manual override (no UI today — SQL only) ──────────────────────────────────
-- UPDATE volume_data_entry.chart_weekly
--    SET value_manual = 1234567, updated_by = 'name.surname@luxottica.com',
--        updated_at = now()
--  WHERE year = 2025 AND week = 18;
-- Set value_manual back to NULL to fall back to value_hist.
