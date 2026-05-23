-- Schema v2.21 migration from v2.20.
--
-- Spec FIX-6 — adds the activity_log table: a system-wide
-- observability feed (discovery / evaluation / generation /
-- application / scheduled-task events with status + timing).
--
-- Distinct from the existing entity-scoped `events` table, which
-- records per-opportunity status transitions. activity_log is the
-- human-facing "what ran, when, did it work" feed.
--
-- Idempotency: gated by `if current == 220:` in
-- tracker._apply_migrations.

CREATE TABLE activity_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    category       TEXT NOT NULL,
    action         TEXT NOT NULL,
    status         TEXT NOT NULL,
    summary        TEXT,
    details        TEXT,
    opportunity_id INTEGER,
    duration_ms    INTEGER,
    error_message  TEXT
);
CREATE INDEX idx_activity_log_ts ON activity_log(timestamp DESC);
CREATE INDEX idx_activity_log_cat ON activity_log(category, timestamp DESC);

UPDATE schema_version SET version = 221;
