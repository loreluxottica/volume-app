-- Add `year` to submissions/drafts so weeks are keyed by (year, week_id).
-- ISO week numbers repeat every year; week_id alone collides at year boundary.
--
-- Deploy order:
--   1. Run this script on Lakebase (databricks_postgres, schema volume_data_entry).
--   2. Deploy the app version that reads/writes `year`.
--   3. Re-run the two UPDATE statements once more, to backfill any rows written
--      by the old code between step 1 and step 2 (new queries filter year = %s
--      and would never see NULL-year rows).
--
-- Backfill via weeks is unambiguous while the weeks table spans a single year.

ALTER TABLE "volume_data_entry".submissions ADD COLUMN year INTEGER;

UPDATE "volume_data_entry".submissions s
SET year = (SELECT MAX(w.year)
            FROM "volume_data_entry".weeks w
            WHERE w.week_id = s.week_id)
WHERE s.year IS NULL;

ALTER TABLE "volume_data_entry".drafts ADD COLUMN year INTEGER;

UPDATE "volume_data_entry".drafts d
SET year = (SELECT MAX(w.year)
            FROM "volume_data_entry".weeks w
            WHERE w.week_id = d.week_id)
WHERE d.year IS NULL;
