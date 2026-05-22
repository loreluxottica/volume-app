-- scripts/create_schema.sql
-- Run once against databricks_postgres to bootstrap the volume_data_entry schema.
-- Connect as a user with CREATE privilege on the database.

CREATE SCHEMA IF NOT EXISTS volume_data_entry;

-- ── weeks ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS volume_data_entry.weeks (
    week_id    INTEGER      NOT NULL,
    year       INTEGER      NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    is_open    BOOLEAN      NOT NULL DEFAULT TRUE,
    PRIMARY KEY (week_id, year)
);

-- ── submissions ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS volume_data_entry.submissions (
    submission_id   TEXT        NOT NULL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    week_id         INTEGER     NOT NULL,
    site            TEXT        NOT NULL,
    product_line    TEXT        NOT NULL,
    user_id         TEXT        NOT NULL,
    submission_type TEXT        NOT NULL,
    channel         TEXT        NOT NULL,
    value_kpcs      DOUBLE PRECISION,
    is_zero_flagged BOOLEAN     NOT NULL DEFAULT FALSE,
    official_log    BOOLEAN     NOT NULL DEFAULT TRUE,
    comment_preset  TEXT,
    comment_other   TEXT,
    is_amendment    BOOLEAN     NOT NULL DEFAULT FALSE,
    ref_submission_id TEXT
);

CREATE INDEX IF NOT EXISTS submissions_lookup
    ON volume_data_entry.submissions (week_id, site, product_line, official_log);

-- ── drafts ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS volume_data_entry.drafts (
    draft_id        TEXT        NOT NULL PRIMARY KEY,
    saved_at        TIMESTAMPTZ NOT NULL,
    week_id         INTEGER     NOT NULL,
    site            TEXT        NOT NULL,
    product_line    TEXT        NOT NULL,
    user_id         TEXT        NOT NULL,
    submission_type TEXT        NOT NULL,
    channel         TEXT        NOT NULL,
    value_kpcs      DOUBLE PRECISION,
    is_zero_flagged BOOLEAN     NOT NULL DEFAULT FALSE,
    comment_preset  TEXT,
    comment_other   TEXT,
    UNIQUE (week_id, site, product_line, user_id, submission_type, channel)
);

-- ── app_access ────────────────────────────────────────────────────────────────
-- Rows grant a user access to a site. site='*' means every site (admin).
-- Managed via SQL directly, never in git.
CREATE TABLE IF NOT EXISTS volume_data_entry.app_access (
    email TEXT NOT NULL,
    site  TEXT NOT NULL,
    PRIMARY KEY (email, site)
);
