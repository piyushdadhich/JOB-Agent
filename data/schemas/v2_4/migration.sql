-- Schema v2.4: Match scores table + skill labels lookup.
--
-- match_scores stores per-opportunity scoring output:
--   - Deterministic fields (always populated by Step 5a scorer)
--   - Gemma fields (NULL until Step 5b scorer runs; some
--     postings never get Gemma if they're in the 'zero' or
--     'low' bucket)
--   - Keyed on (opportunity_id, scorer_version) so re-running
--     a different scorer version produces a parallel row.
--
-- skill_labels is a flat lookup table for human-readable skill
-- names. Populated by re-running the backfill, which now also
-- collects (skill_id, label, taxonomy, match_type) tuples from
-- the extractor output. Used by Step 5b's Gemma prompt to send
-- labels rather than opaque IDs.
--
-- Migration is purely additive. No data is rewritten.

CREATE TABLE match_scores (
    opportunity_id            INTEGER NOT NULL
                              REFERENCES opportunities(id),
    scorer_version            TEXT NOT NULL,
    -- Deterministic layer (always populated)
    overlap_count             INTEGER NOT NULL,
    posting_skill_count       INTEGER NOT NULL,
    inventory_skill_count     INTEGER NOT NULL,
    coverage_raw              REAL NOT NULL,
    coverage_idf              REAL NOT NULL,
    overlap_skill_ids         TEXT NOT NULL,   -- JSON array
    missed_skill_ids          TEXT NOT NULL,   -- JSON array
    bucket                    TEXT NOT NULL
                              CHECK (bucket IN (
                                  'zero', 'low', 'high')),
    -- Gemma layer (NULL until Step 5b runs)
    gemma_score               INTEGER,
    gemma_top_matches         TEXT,            -- JSON array
    gemma_transferable        TEXT,            -- JSON array
    gemma_critical_gaps       TEXT,            -- JSON array
    gemma_summary             TEXT,
    gemma_hallucination_flags INTEGER,
    gemma_raw_response        TEXT,
    -- Metadata
    scored_at                 TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, scorer_version)
);

CREATE INDEX idx_match_scores_bucket
    ON match_scores(bucket);
CREATE INDEX idx_match_scores_coverage_idf
    ON match_scores(coverage_idf);

CREATE TABLE skill_labels (
    skill_id    TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    taxonomy    TEXT NOT NULL,
    match_type  TEXT,
    source      TEXT NOT NULL DEFAULT 'backfill'
);

UPDATE schema_version SET version = 24;
