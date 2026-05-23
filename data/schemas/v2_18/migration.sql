-- Schema v2.18 migration from v2.17.
--
-- Spec H1 — Phase 9 Targeted Outreach Agent.
--
-- Adds six new tables for person-level outreach: persons,
-- person_emails, person_scores, outreach_targets,
-- company_email_formats, do_not_contact. All purely additive.
--
-- Idempotency: this file is only executed when schema_version=217
-- (guarded by tracker._apply_migrations). SQLite has no
-- IF NOT EXISTS for ALTER/CREATE TABLE here for the version gate
-- to suffice, but CREATE TABLE IF NOT EXISTS is used as a defensive
-- belt-and-braces against re-runs outside the gate.

CREATE TABLE IF NOT EXISTS persons (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    source                   TEXT NOT NULL,
    source_url               TEXT,
    full_name                TEXT NOT NULL,
    headline                 TEXT,
    current_employer         TEXT,
    current_title            TEXT,
    linkedin_url             TEXT UNIQUE,
    github_username          TEXT UNIQUE,
    email_primary            TEXT,
    email_source             TEXT,
    email_confidence         TEXT,
    location                 TEXT,
    personalization_hooks    TEXT,
    raw_payload              TEXT,
    date_discovered          TEXT NOT NULL,
    last_seen_at             TEXT NOT NULL,
    company_id               INTEGER REFERENCES companies(id),
    job_poster_id            INTEGER REFERENCES job_posters(id)
);
CREATE INDEX IF NOT EXISTS idx_persons_company_id ON persons(company_id);
CREATE INDEX IF NOT EXISTS idx_persons_source ON persons(source);
CREATE INDEX IF NOT EXISTS idx_persons_email_primary ON persons(email_primary);

CREATE TABLE IF NOT EXISTS person_emails (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id         INTEGER NOT NULL REFERENCES persons(id),
    email             TEXT NOT NULL,
    source            TEXT NOT NULL,
    source_url        TEXT,
    confidence        TEXT NOT NULL,
    discovered_at     TEXT NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    UNIQUE(person_id, email)
);
CREATE INDEX IF NOT EXISTS idx_person_emails_person_id ON person_emails(person_id);
CREATE INDEX IF NOT EXISTS idx_person_emails_email ON person_emails(email);

CREATE TABLE IF NOT EXISTS person_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id           INTEGER NOT NULL REFERENCES persons(id),
    score_overall       REAL,
    score_read_rate     REAL,
    score_reachability  REAL,
    score_fit           REAL,
    tier                TEXT,
    reasoning           TEXT,
    scorer_version      TEXT,
    scored_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_person_scores_person_id ON person_scores(person_id);
CREATE INDEX IF NOT EXISTS idx_person_scores_tier ON person_scores(tier);

CREATE TABLE IF NOT EXISTS outreach_targets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id         INTEGER NOT NULL REFERENCES persons(id),
    opportunity_id    INTEGER REFERENCES opportunities(id),
    status            TEXT NOT NULL DEFAULT 'pending',
    message_subject   TEXT,
    message_body      TEXT,
    template_used     TEXT,
    sent_at           TEXT,
    sent_channel      TEXT,
    response_at       TEXT,
    response_text     TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_targets_person_id ON outreach_targets(person_id);
CREATE INDEX IF NOT EXISTS idx_outreach_targets_status ON outreach_targets(status);
CREATE INDEX IF NOT EXISTS idx_outreach_targets_sent_at ON outreach_targets(sent_at);

CREATE TABLE IF NOT EXISTS company_email_formats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    format_pattern  TEXT NOT NULL,
    confidence      REAL NOT NULL,
    sample_count    INTEGER NOT NULL,
    inferred_at     TEXT NOT NULL,
    UNIQUE(company_id)
);

CREATE TABLE IF NOT EXISTS do_not_contact (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier_type   TEXT NOT NULL,
    identifier_value  TEXT NOT NULL,
    reason            TEXT NOT NULL,
    added_at          TEXT NOT NULL,
    UNIQUE(identifier_type, identifier_value)
);

UPDATE schema_version SET version = 218;
