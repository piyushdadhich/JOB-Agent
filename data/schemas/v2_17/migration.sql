-- Schema v2.17 migration from v2.16.
--
-- Adds applications.follow_ups (TEXT, nullable) -- JSON list of
-- follow-up email events appended each time the user sends a
-- follow-up from the Pipeline Tracker. Each entry shape:
--   {"sent_at": ISO8601, "to": str, "subject": str,
--    "message_id": str, "template": str}
--
-- NULL is the cold-start state (no follow-ups sent yet); the
-- pipeline_tracker write path uses json_array() / json('[]')
-- depending on whether the column has an existing value.
--
-- Idempotency: this file is only executed when schema_version=216
-- (guarded by tracker._apply_migrations). SQLite has no
-- IF NOT EXISTS for ALTER TABLE, so re-running this script outside
-- the gate would fail.

ALTER TABLE applications ADD COLUMN follow_ups TEXT;

UPDATE schema_version SET version = 217;
