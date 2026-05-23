-- Job-agent tracker schema v2.2.
--
-- Changes from v2:
--   * opportunities.extracted_skill_ids TEXT (JSON array of taxonomy
--     skill IDs, populated by Phase 5d skill extraction).
--   * companies.inventory_skill_ids TEXT (reserved; per architecture
--     v4 §6 inventory skill IDs live in profile_skills, not companies).
--   * profile_skills table keyed on (profile_id, taxonomy) holding the
--     career-inventory skill extraction per profile per taxonomy.
--
-- All timestamps are ISO 8601 UTC strings.

PRAGMA foreign_keys = ON;

-- 1. Companies — referenced by opportunities and (later) eval_decisions.
CREATE TABLE companies (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    name_normalized         TEXT NOT NULL UNIQUE,
    industry                TEXT,
    size_label              TEXT,
    size_bucket             TEXT,
    sector                  TEXT,
    culture_summary         TEXT,
    culture_status          TEXT,
    known_concerns          TEXT,
    last_culture_check_at   TEXT,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    notes                   TEXT,
    inventory_skill_ids     TEXT
);
CREATE INDEX idx_companies_name_normalized ON companies(name_normalized);

-- 2. Opportunities — aligned to engine.discovery.base.OpportunityRecord.
CREATE TABLE opportunities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id           INTEGER NOT NULL REFERENCES companies(id),
    source               TEXT NOT NULL,
    source_id            TEXT,
    source_url           TEXT NOT NULL,
    url_hash             TEXT NOT NULL UNIQUE,
    title                TEXT NOT NULL,
    location             TEXT,
    posting_text         TEXT,
    is_remote            INTEGER,
    posted_at            TEXT,
    salary_min           REAL,
    salary_max           REAL,
    salary_currency      TEXT,
    salary_interval      TEXT,
    raw_payload          TEXT,
    search_context       TEXT,
    date_discovered      TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                             'new', 'shortlisted', 'dismissed', 'pursued')),
    notes                TEXT,
    tags                 TEXT,
    extracted_skill_ids  TEXT
);
CREATE INDEX idx_opportunities_url_hash      ON opportunities(url_hash);
CREATE INDEX idx_opportunities_company_id    ON opportunities(company_id);
CREATE INDEX idx_opportunities_discovered_at ON opportunities(date_discovered);
CREATE INDEX idx_opportunities_status        ON opportunities(status);

-- 3. Eval decisions — per-opportunity scoring/classification output.
CREATE TABLE eval_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id      INTEGER NOT NULL REFERENCES opportunities(id),
    evaluator_version   TEXT NOT NULL,
    tier                TEXT NOT NULL CHECK (tier IN (
                            'TOP_TIER', 'STRONG', 'EXPLORATORY',
                            'SKIP', 'EXCLUDED')),
    fit_score           INTEGER CHECK (fit_score IS NULL OR
                            (fit_score BETWEEN 1 AND 10)),
    sector              TEXT,
    role_type           TEXT,
    stage_trace         TEXT NOT NULL,
    reasoning           TEXT,
    evaluated_at        TEXT NOT NULL
);
CREATE INDEX idx_eval_decisions_opportunity_id ON eval_decisions(opportunity_id);
CREATE INDEX idx_eval_decisions_tier           ON eval_decisions(tier);
CREATE INDEX idx_eval_decisions_evaluated_at   ON eval_decisions(evaluated_at);

-- 4. Recruiters / agencies.
CREATE TABLE recruiters (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    agency_name             TEXT NOT NULL,
    contact_name            TEXT,
    contact_email           TEXT,
    contact_phone           TEXT,
    linkedin_url            TEXT,
    first_contact_date      TEXT NOT NULL,
    resume_variant_shared   TEXT,
    notes                   TEXT
);
CREATE INDEX idx_recruiters_agency_name ON recruiters(agency_name);

-- 5. Applications.
CREATE TABLE applications (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id          INTEGER NOT NULL REFERENCES opportunities(id),
    resume_variant          TEXT NOT NULL,
    cover_letter_path       TEXT,
    federal_responses_path  TEXT,
    submitted_date          TEXT,
    submitted_via           TEXT CHECK (submitted_via IS NULL OR submitted_via IN (
                                'direct', 'recruiter')),
    recruiter_id            INTEGER REFERENCES recruiters(id),
    status                  TEXT NOT NULL DEFAULT 'drafted' CHECK (status IN (
                                'drafted', 'ready_to_submit', 'submitted',
                                'confirmed_received', 'responded', 'interviewing',
                                'offered', 'rejected', 'ghosted',
                                'silent_rejected', 'withdrawn')),
    status_updated_at       TEXT NOT NULL,
    tags                    TEXT,
    notes                   TEXT
);
CREATE INDEX idx_applications_opportunity_id ON applications(opportunity_id);
CREATE INDEX idx_applications_status         ON applications(status);
CREATE INDEX idx_applications_submitted_date ON applications(submitted_date);

-- 6. Status history.
CREATE TABLE status_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id),
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    changed_at      TEXT NOT NULL,
    reason          TEXT
);
CREATE INDEX idx_status_history_application_id ON status_history(application_id);
CREATE INDEX idx_status_history_changed_at     ON status_history(changed_at);

-- 7. Communications.
CREATE TABLE communications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id      INTEGER NOT NULL REFERENCES applications(id),
    channel             TEXT NOT NULL CHECK (channel IN (
                            'email', 'phone', 'linkedin', 'in_person',
                            'video_call', 'ats_automated')),
    direction           TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    occurred_at         TEXT NOT NULL,
    summary             TEXT NOT NULL,
    followup_needed     INTEGER NOT NULL DEFAULT 0 CHECK (followup_needed IN (0, 1)),
    followup_by         TEXT,
    notes               TEXT
);
CREATE INDEX idx_communications_application_id ON communications(application_id);
CREATE INDEX idx_communications_occurred_at    ON communications(occurred_at);
CREATE INDEX idx_communications_followup_pending
    ON communications(followup_by) WHERE followup_needed = 1;

-- 8. Events.
CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at     TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'opportunity_discovered', 'opportunity_seen_again',
                        'opportunity_classified', 'opportunity_scored',
                        'opportunity_dismissed', 'company_upserted',
                        'application_drafted', 'application_submitted',
                        'status_changed', 'communication_logged',
                        'email_received', 'error')),
    entity_type     TEXT CHECK (entity_type IS NULL OR entity_type IN (
                        'opportunity', 'application', 'recruiter',
                        'communication', 'company')),
    entity_id       INTEGER,
    summary         TEXT NOT NULL,
    details         TEXT
);
CREATE INDEX idx_events_occurred_at ON events(occurred_at);
CREATE INDEX idx_events_type        ON events(event_type);

-- 9. Profile skills (career inventory extraction, per profile per taxonomy).
CREATE TABLE profile_skills (
    profile_id     TEXT NOT NULL,
    taxonomy       TEXT NOT NULL,
    skill_ids      TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    source_doc     TEXT NOT NULL,
    raw_extraction TEXT,
    PRIMARY KEY (profile_id, taxonomy)
);

-- Schema version marker.
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version (version) VALUES (22);
