-- Job-agent tracker schema v2.20.
--
-- Changes from v2.19:
--   * Spec JA-1 TASK 2 — adds four columns to eval_decisions:
--     letter_grade (TEXT) — A/B/C/D/F mapping of fit_score for
--       human-readable communication. Coexists with tier.
--     interview_plan (TEXT, JSON list) — 3 talking points generated
--       by Gemma for A/B-graded postings.
--     red_flags (TEXT, JSON list) — deterministic per-posting risk
--       flags ({flag, severity}).
--     culture_signals (TEXT, JSON list) — Gemma-generated culture
--       reads ({signal, sentiment}) for A/B grades.
--     All additive, all nullable. No existing columns changed.
--
-- (Earlier v2.19 changes retained below for reference.)
-- Changes from v2.18:
--   * Add persons.personal_site_url (TEXT, nullable) — captures the
--     `blog` field from GitHub profiles, or a personal-website URL
--     parsed from a conference speaker bio. Drives the email_finder's
--     personal-site fallback in TASK 3.
--
-- (Earlier v2.18 changes retained below for reference.)
-- Changes from v2.17:
--   * Spec H1 — Phase 9 Targeted Outreach Agent. Adds six new tables:
--     persons (canonical individuals at target companies),
--     person_emails (multi-source emails per person),
--     person_scores (three-signal read-rate / reachability / fit),
--     outreach_targets (the workflow row for an actual send),
--     company_email_formats (inferred per-company email patterns),
--     do_not_contact (suppress list keyed by email/domain/etc).
--     All purely additive. No existing columns changed.
--
-- (Earlier v2.17 changes retained below for reference.)
-- Changes from v2.16:
--   * Add applications.follow_ups (TEXT, nullable) — JSON list of
--     follow-up email events appended each time the user sends a
--     follow-up from the Pipeline Tracker. Each entry shape:
--       {"sent_at": ISO8601, "to": str, "subject": str,
--        "message_id": str, "template": str}
--     NULL means "no follow-ups have been sent yet" (cheaper than
--     storing "[]" for every row in the cold-start state).
--
-- (Earlier v2.16 changes retained below for reference.)
-- Changes from v2.15:
--   * Add companies.is_deep_target (INTEGER NOT NULL DEFAULT 0) —
--     0/1 flag marking a company as a "go-deep" target. Flipped to 1
--     by the user after reviewing the weekly expansion report; the
--     downstream ATS catalog fetcher pulls all open roles from
--     flagged companies regardless of title filter.
--
-- (Earlier v2.15 changes retained below for reference.)
-- Changes from v2.14:
--   * Add profile_skills.skill_count (INTEGER NOT NULL DEFAULT 0) —
--     denormalized cache of len(JSON skill_ids) for cheap aggregate
--     reads without parsing the JSON column. Backfilled lazily by
--     extract_inventory_skills.py / upsert_profile_skills callers;
--     defaults to 0 for any row the migration touches.
--
-- (Earlier v2.14 changes retained below for reference.)
-- Changes from v2.13:
--   * Add opportunities.hiring_team_json (TEXT, nullable) — JSON-
--     serialized list of hiring-team members observed on the posting
--     detail page. Surfaced primarily by linkedin_guest source.
--   * Add new job_posters table — canonical record of an individual
--     person who has posted at least one role we've seen. Idempotent
--     on linkedin_profile_url when present, otherwise on
--     (full_name, employer_at_posting). Tracks first/last seen and
--     total observed postings.
--   * Add new opportunity_posters table — many-to-many join between
--     opportunities and job_posters. role_on_posting captures whether
--     this person appeared in the "Meet the hiring team" card
--     (role='hiring_team') or in the "Posted by" byline
--     (role='posted_by'). Composite primary key allows the same
--     person to appear in both roles on the same posting.
--
-- All additions are additive only — no existing columns are dropped
-- or modified. Foundation for Phase 9 targeted outreach work.
--
-- All timestamps are ISO 8601 UTC strings.

PRAGMA foreign_keys = ON;

-- 1. Companies -- referenced by opportunities and (later) eval_decisions.
CREATE TABLE companies (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    name                          TEXT NOT NULL,
    name_normalized               TEXT NOT NULL UNIQUE,
    industry                      TEXT,
    size_bucket                   TEXT,
    sector                        TEXT,
    culture_summary               TEXT,
    culture_status                TEXT,
    known_concerns                TEXT,
    last_culture_check_at         TEXT,
    first_seen_at                 TEXT NOT NULL,
    last_seen_at                  TEXT NOT NULL,
    notes                         TEXT,
    inventory_skill_ids           TEXT,
    canonical_domain              TEXT,
    ats_platform                  TEXT,
    ats_slug                      TEXT,
    ats_detected_at               TEXT,
    ats_detection_method          TEXT,
    ats_detection_confidence      REAL,
    is_deep_target                INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_companies_name_normalized ON companies(name_normalized);
CREATE INDEX idx_companies_ats_platform    ON companies(ats_platform);
CREATE INDEX idx_companies_is_deep_target  ON companies(is_deep_target);

-- 2. Opportunities.
CREATE TABLE opportunities (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id                      INTEGER NOT NULL REFERENCES companies(id),
    source                          TEXT NOT NULL,
    source_id                       TEXT,
    source_url                      TEXT NOT NULL,
    url_hash                        TEXT NOT NULL UNIQUE,
    title                           TEXT NOT NULL,
    location                        TEXT,
    posting_text                    TEXT,
    is_remote                       INTEGER,
    posted_at                       TEXT,
    salary_min                      REAL,
    salary_max                      REAL,
    salary_currency                 TEXT,
    salary_interval                 TEXT,
    raw_payload                     TEXT,
    search_context                  TEXT,
    date_discovered                 TEXT NOT NULL,
    last_seen_at                    TEXT NOT NULL,
    status                          TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                                        'new', 'shortlisted', 'dismissed', 'pursued')),
    extracted_skill_ids             TEXT,
    extracted_skill_ids_secondary   TEXT,
    secondary_taxonomy              TEXT,
    -- v2.11 classification columns:
    function                        TEXT,
    industry_normalized             TEXT,
    city                            TEXT,
    ai_subtype                      TEXT,
    eval_priority                   INTEGER DEFAULT 2,
    -- v2.14 hiring team payload (JSON list of {name, title, profile_url, role}):
    hiring_team_json                TEXT
);
CREATE INDEX idx_opportunities_url_hash       ON opportunities(url_hash);
CREATE INDEX idx_opportunities_company_id     ON opportunities(company_id);
CREATE INDEX idx_opportunities_discovered_at  ON opportunities(date_discovered);
CREATE INDEX idx_opportunities_status         ON opportunities(status);
CREATE INDEX idx_opportunities_eval_priority  ON opportunities(eval_priority);
CREATE INDEX idx_opportunities_city           ON opportunities(city);
CREATE INDEX idx_opportunities_ai_subtype     ON opportunities(ai_subtype);

-- 3. Eval decisions.
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
    evaluated_at        TEXT NOT NULL,
    -- v2.12 user-flag stamp (set when a user flags this decision
    -- as bad-match; NULL otherwise, including for rule-skip-v1
    -- auto-skips since those aren't user flags).
    flagged_at          TEXT,
    -- v2.20 Spec JA-1: human-facing letter grade + reasoning extras.
    letter_grade        TEXT,
    interview_plan      TEXT,
    red_flags           TEXT,
    culture_signals     TEXT
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
    notes                   TEXT,
    -- v2.9:
    resume_text             TEXT,
    cover_letter_text       TEXT,
    ats_platform            TEXT,
    screenshot_path         TEXT,
    submitted_url           TEXT,
    screening_answers       TEXT,
    selected_at             TEXT,
    prompt_generated_at     TEXT,
    docs_ready_at           TEXT,
    -- v2.10:
    resume_prompt           TEXT,
    cover_letter_prompt     TEXT,
    -- v2.17 follow-up email log (JSON list of {sent_at,to,subject,message_id,template}):
    follow_ups              TEXT
);
CREATE INDEX idx_applications_opportunity_id ON applications(opportunity_id);
CREATE INDEX idx_applications_status         ON applications(status);
CREATE INDEX idx_applications_submitted_date ON applications(submitted_date);
CREATE INDEX idx_applications_ats_platform   ON applications(ats_platform);

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

-- 9. Profile skills.
CREATE TABLE profile_skills (
    profile_id     TEXT NOT NULL,
    taxonomy       TEXT NOT NULL,
    skill_ids      TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    source_doc     TEXT NOT NULL,
    raw_extraction TEXT,
    skill_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, taxonomy)
);

-- 10. Match scores (v2.4).
CREATE TABLE match_scores (
    opportunity_id            INTEGER NOT NULL
                              REFERENCES opportunities(id),
    scorer_version            TEXT NOT NULL,
    overlap_count             INTEGER NOT NULL,
    posting_skill_count       INTEGER NOT NULL,
    inventory_skill_count     INTEGER NOT NULL,
    coverage_raw              REAL NOT NULL,
    coverage_idf              REAL NOT NULL,
    overlap_skill_ids         TEXT NOT NULL,
    missed_skill_ids          TEXT NOT NULL,
    bucket                    TEXT NOT NULL
                              CHECK (bucket IN (
                                  'zero', 'low', 'high')),
    gemma_score               INTEGER,
    gemma_top_matches         TEXT,
    gemma_transferable        TEXT,
    gemma_critical_gaps       TEXT,
    gemma_summary             TEXT,
    gemma_hallucination_flags INTEGER,
    gemma_raw_response        TEXT,
    scored_at                 TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, scorer_version)
);
CREATE INDEX idx_match_scores_bucket       ON match_scores(bucket);
CREATE INDEX idx_match_scores_coverage_idf ON match_scores(coverage_idf);

-- 11. Skill labels (v2.4).
CREATE TABLE skill_labels (
    skill_id    TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    taxonomy    TEXT NOT NULL,
    match_type  TEXT,
    source      TEXT NOT NULL DEFAULT 'backfill'
);

-- 12. Eval labels (v2.5).
CREATE TABLE eval_labels (
    opportunity_id  INTEGER PRIMARY KEY
                    REFERENCES opportunities(id),
    verdict         TEXT NOT NULL CHECK (verdict IN (
                        'shortlist', 'skip', 'unsure')),
    reason          TEXT,
    notes           TEXT,
    labeled_at      TEXT NOT NULL,
    labeled_by      TEXT NOT NULL DEFAULT 'default'
);

-- 13. Stage decisions (v2.6).
CREATE TABLE stage_decisions (
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    stage_name      TEXT NOT NULL,
    stage_version   TEXT NOT NULL,
    decision        TEXT NOT NULL,
    reason          TEXT,
    metadata        TEXT,
    decided_at      TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, stage_name, stage_version)
);
CREATE INDEX idx_stage_decisions_decision
    ON stage_decisions(decision);
CREATE INDEX idx_stage_decisions_stage
    ON stage_decisions(stage_name, stage_version);

-- 14. Outreach drafts (v2.8).
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

-- 15. Flag rules (v2.12).
CREATE TABLE flag_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_pattern TEXT,
    title_pattern TEXT,
    industry_pattern TEXT,
    function_pattern TEXT,
    ai_subtype_pattern TEXT,
    source_opportunity_id INTEGER,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    FOREIGN KEY (source_opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX idx_flag_rules_active ON flag_rules(active);

-- 16. Job posters (v2.14) -- canonical record of an individual who has
-- posted at least one role we've discovered. Idempotent on
-- linkedin_profile_url when present, otherwise on
-- (full_name, employer_at_posting). total_postings_observed increments
-- each time we re-observe the same person.
CREATE TABLE job_posters (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    linkedin_profile_url     TEXT UNIQUE,
    full_name                TEXT NOT NULL,
    title_at_posting         TEXT,
    employer_at_posting      TEXT,
    company_id               INTEGER REFERENCES companies(id),
    first_seen_at            TEXT NOT NULL,
    last_seen_at             TEXT NOT NULL,
    total_postings_observed  INTEGER DEFAULT 1
);
CREATE INDEX idx_job_posters_url           ON job_posters(linkedin_profile_url);
CREATE INDEX idx_job_posters_name_employer ON job_posters(full_name, employer_at_posting);

-- 17. Opportunity posters (v2.14) -- many-to-many join between
-- opportunities and job_posters. role_on_posting is 'hiring_team' (from
-- the "Meet the hiring team" card section) or 'posted_by' (from the
-- "Posted by ..." top-card byline). Composite PK lets the same person
-- appear in both roles on the same posting (idempotent on re-observation).
CREATE TABLE opportunity_posters (
    opportunity_id   INTEGER NOT NULL REFERENCES opportunities(id),
    job_poster_id    INTEGER NOT NULL REFERENCES job_posters(id),
    role_on_posting  TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, job_poster_id, role_on_posting)
);

-- 18. Persons (v2.18 / Spec H1) — canonical record of an individual
-- the user might contact for cold outreach. Idempotent on
-- linkedin_url first, github_username second. Sourced from
-- 'github', 'linkedin_hiring_team', 'company_page', 'manual', etc.
CREATE TABLE persons (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    source                   TEXT NOT NULL,
    source_url               TEXT,
    full_name                TEXT NOT NULL,
    headline                 TEXT,
    current_employer         TEXT,
    current_title            TEXT,
    linkedin_url             TEXT UNIQUE,
    github_username          TEXT UNIQUE,
    personal_site_url        TEXT,
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
CREATE INDEX idx_persons_company_id    ON persons(company_id);
CREATE INDEX idx_persons_source        ON persons(source);
CREATE INDEX idx_persons_email_primary ON persons(email_primary);

-- 19. Person emails (v2.18) — multi-source email discovery log.
-- A single person may have multiple discovered emails over time
-- (e.g. one from GitHub commits, one inferred from a company
-- format pattern); is_active=0 marks bounces/invalid addresses.
CREATE TABLE person_emails (
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
CREATE INDEX idx_person_emails_person_id ON person_emails(person_id);
CREATE INDEX idx_person_emails_email     ON person_emails(email);

-- 20. Person scores (v2.18) — three-signal scorer output.
-- read_rate (will they open the repo?), reachability (do we have
-- email/LinkedIn?), fit (is their company a target?). Tiers map
-- 6.0+ to REACH_OUT, 3.5+ to RESEARCH_MORE, anything else SKIP.
CREATE TABLE person_scores (
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
CREATE INDEX idx_person_scores_person_id ON person_scores(person_id);
CREATE INDEX idx_person_scores_tier      ON person_scores(tier);

-- 21. Outreach targets (v2.18) — the workflow row for an actual
-- send attempt. One per (person, opportunity) pair. status tracks
-- the full lifecycle: pending -> draft_ready -> sent_* -> responded.
CREATE TABLE outreach_targets (
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
CREATE INDEX idx_outreach_targets_person_id ON outreach_targets(person_id);
CREATE INDEX idx_outreach_targets_status    ON outreach_targets(status);
CREATE INDEX idx_outreach_targets_sent_at   ON outreach_targets(sent_at);

-- 22. Company email formats (v2.18) — inferred per-company email
-- pattern (first.last@company.com, flast@, etc.) with a sample
-- count + confidence so the email finder can decide whether to
-- trust the pattern. One row per company.
CREATE TABLE company_email_formats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    format_pattern  TEXT NOT NULL,
    confidence      REAL NOT NULL,
    sample_count    INTEGER NOT NULL,
    inferred_at     TEXT NOT NULL,
    UNIQUE(company_id)
);

-- 23. Do not contact (v2.18) — suppression list. identifier_type
-- is one of {'email', 'domain', 'person_id', 'linkedin_url'};
-- identifier_value the corresponding key. Checked before every
-- send by health_check.
CREATE TABLE do_not_contact (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier_type   TEXT NOT NULL,
    identifier_value  TEXT NOT NULL,
    reason            TEXT NOT NULL,
    added_at          TEXT NOT NULL,
    UNIQUE(identifier_type, identifier_value)
);

-- Schema version marker.
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version (version) VALUES (220);
