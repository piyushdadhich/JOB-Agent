-- Schema v2.16 migration from v2.15.
--
-- Adds companies.is_deep_target (0/1 flag) for the Spec D1 TASK 2
-- "go-deep" employer promotion strategy. User flips this to 1 after
-- reviewing the weekly expansion report; the downstream ATS catalog
-- fetcher pulls every open role for flagged companies regardless of
-- title filter.
--
-- Idempotency: this file is only executed when schema_version=215
-- (guarded by tracker._apply_migrations). SQLite has no
-- IF NOT EXISTS for ALTER TABLE, so re-running this script outside
-- the gate would fail.

ALTER TABLE companies ADD COLUMN is_deep_target INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_companies_is_deep_target
    ON companies(is_deep_target);

UPDATE schema_version SET version = 216;
