-- Schema v2.14: LinkedIn hiring-team support (Phase 5a Step 7, Spec A2).
--
-- All additions are additive only — no existing columns or rows touched.
--
-- New:
--   * opportunities.hiring_team_json TEXT (nullable)
--   * job_posters table + indexes
--   * opportunity_posters table (PK = (opportunity_id, job_poster_id,
--     role_on_posting); idempotency comes from PK constraint).
--
-- Idempotency for the ALTER TABLE comes from the "if current == 213"
-- gate in tracker._apply_migrations (same pattern as v2_10..v2_13).

ALTER TABLE opportunities ADD COLUMN hiring_team_json TEXT;

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

CREATE TABLE opportunity_posters (
    opportunity_id   INTEGER NOT NULL REFERENCES opportunities(id),
    job_poster_id    INTEGER NOT NULL REFERENCES job_posters(id),
    role_on_posting  TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, job_poster_id, role_on_posting)
);

UPDATE schema_version SET version = 214;
