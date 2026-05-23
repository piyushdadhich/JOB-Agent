-- Schema v2.2: skill extraction columns
-- Adds JSON columns for extracted skill IDs and a profile_skills
-- table. No data is rewritten; migration is purely additive.

ALTER TABLE opportunities ADD COLUMN extracted_skill_ids TEXT;
-- TEXT not JSON because SQLite stores JSON as TEXT and we want
-- compatibility across older sqlite builds. Application layer
-- parses.

ALTER TABLE companies ADD COLUMN inventory_skill_ids TEXT;
-- Currently unused at company level; placeholder for future
-- "company-level skill profile" feature. Per architecture v4 §6,
-- inventory skill IDs live in profile_skills, not on companies.
-- This column is reserved space, not used by Step 1-3.

CREATE TABLE IF NOT EXISTS profile_skills (
    profile_id     TEXT NOT NULL,
    taxonomy       TEXT NOT NULL,
    skill_ids      TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    source_doc     TEXT NOT NULL,
    raw_extraction TEXT,
    PRIMARY KEY (profile_id, taxonomy)
);

UPDATE schema_version SET version = 22;
