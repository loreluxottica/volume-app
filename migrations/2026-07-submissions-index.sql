-- migrations/2026-07-submissions-index.sql
-- Supporting index for the Landings page's yearly aggregate
-- (data/db.py::get_landings_weekly): a ROW_NUMBER window over every official row
-- of a year, re-run whenever the 5-minute cache expires. Without it that is a
-- sequential scan of the whole submissions table on a shared connection, which
-- blocks every other user (gunicorn runs a single worker).
--
-- Partial on official_log because every read path filters on it and the
-- superseded rows are dead weight for queries (they only matter as an audit trail).

CREATE INDEX IF NOT EXISTS submissions_year_week_official_idx
  ON volume_data_entry.submissions (year, week_id)
  WHERE official_log;
