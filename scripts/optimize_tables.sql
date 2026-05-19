-- scripts/optimize_tables.sql
-- ---------------------------------------------------------------------------
-- DB query optimization for the Volumes Data Entry Tool (BBP v0.7 item #11).
--
-- The `submissions` table grows to tens of thousands of rows per year. Every
-- read query in data/db.py filters by week_id (+ site, product_line), so
-- clustering the table on those keys lets Delta prune to one week's files
-- instead of scanning the whole table.
--
-- Run once in a Databricks SQL editor / notebook against the existing tables,
-- then schedule the OPTIMIZE / VACUUM block below as a weekly Databricks Job.
-- ---------------------------------------------------------------------------

-- ── Liquid clustering ───────────────────────────────────────────────────────
-- Keys match the WHERE clauses of get_latest_submissions / get_gli_extract
-- (submissions) and get_drafts / get_draft (drafts).

ALTER TABLE `sbx-logistics`.`volume-data-entry-app`.submissions
  CLUSTER BY (week_id, site, product_line);

ALTER TABLE `sbx-logistics`.`volume-data-entry-app`.drafts
  CLUSTER BY (week_id, site, product_line, user_id);

-- ── Compaction ──────────────────────────────────────────────────────────────
-- OPTIMIZE applies the clustering and compacts small files. submit_row's
-- official_log UPDATE and the per-Save DELETE+INSERT on `drafts` fragment the
-- files over time, so re-run this on a schedule (weekly Databricks Job).

OPTIMIZE `sbx-logistics`.`volume-data-entry-app`.submissions;
OPTIMIZE `sbx-logistics`.`volume-data-entry-app`.drafts;

-- Remove files no longer referenced (default 7-day retention). Run after
-- OPTIMIZE, on the same schedule.
VACUUM `sbx-logistics`.`volume-data-entry-app`.submissions;
VACUUM `sbx-logistics`.`volume-data-entry-app`.drafts;

-- ── Verify ──────────────────────────────────────────────────────────────────
-- DESCRIBE DETAIL should list the clustering columns under `clusteringColumns`.
--   DESCRIBE DETAIL `sbx-logistics`.`volume-data-entry-app`.submissions;

-- ── Fallback ────────────────────────────────────────────────────────────────
-- If the Databricks Runtime is too old for liquid clustering, recreate the
-- tables partitioned instead:
--   ... PARTITIONED BY (week_id)
--   followed by:  OPTIMIZE <table> ZORDER BY (site, product_line);
