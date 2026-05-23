-- Schema v2.8: outreach_drafts table (Phase 9 — Targeted Outreach Agent).
--
-- Purely additive: new table + indexes + version bump. No existing
-- rows touched.
--
-- Status transitions (enforced in tracker, not the DB):
--   draft -> approved -> sent
--   draft -> rejected
-- `reason` records why this company was selected
-- (historical_match | manually_flagged) -- pure audit metadata.

CREATE TABLE outreach_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    company_name  TEXT NOT NULL,
    subject       TEXT,
    body          TEXT,
    reason        TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                        'draft', 'approved', 'rejected', 'sent')),
    generated_at  TEXT NOT NULL,
    reviewed_at   TEXT,
    sent_at       TEXT
);
CREATE INDEX idx_outreach_drafts_company_id   ON outreach_drafts(company_id);
CREATE INDEX idx_outreach_drafts_status       ON outreach_drafts(status);
CREATE INDEX idx_outreach_drafts_generated_at ON outreach_drafts(generated_at);

UPDATE schema_version SET version = 28;
