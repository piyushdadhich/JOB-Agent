-- Schema v2.15 migration from v2.14.
--
-- Adds profile_skills.skill_count for cheap aggregate reads.
-- The substantive Phase 5d Step 2 work (extracted_skill_ids on
-- opportunities and the profile_skills table itself) was already
-- shipped in v2.2 and v2.4-v2.7. This lean v2.15 captures the only
-- profile_skills column the Spec B1 TASK 2 design called out that
-- did not already exist.
--
-- Idempotency: this file is only executed when schema_version=214
-- (guarded by tracker._apply_migrations). SQLite has no
-- IF NOT EXISTS for ALTER TABLE, so re-running this script outside
-- the gate would fail.

ALTER TABLE profile_skills ADD COLUMN skill_count INTEGER NOT NULL DEFAULT 0;

UPDATE schema_version SET version = 215;
