-- Schema v2.11: classification columns on opportunities.
--
-- Adds five flat classification columns so:
--   * Shortlist filter UI reads city/function/industry directly
--     instead of recomputing LIKE patterns at query time.
--   * The two-stage evaluator routes by eval_priority at the call
--     site (1 = AI direct to cloud; 2 = standard local pre-filter).
--
-- The Python backfill (data/schemas/v2_11/backfill.py) runs after
-- this SQL via tracker._apply_migrations -- function and
-- industry_normalized are pulled out of eval_decisions.reasoning
-- JSON when those keys exist, which is too clumsy in pure SQL.
--
-- All five columns are nullable. eval_priority defaults to 2 so
-- the standard route applies to any row before classification runs.

ALTER TABLE opportunities ADD COLUMN function            TEXT;
ALTER TABLE opportunities ADD COLUMN industry_normalized TEXT;
ALTER TABLE opportunities ADD COLUMN city                TEXT;
ALTER TABLE opportunities ADD COLUMN ai_subtype          TEXT;
ALTER TABLE opportunities ADD COLUMN eval_priority       INTEGER DEFAULT 2;

CREATE INDEX IF NOT EXISTS idx_opportunities_eval_priority
    ON opportunities(eval_priority);
CREATE INDEX IF NOT EXISTS idx_opportunities_city
    ON opportunities(city);
CREATE INDEX IF NOT EXISTS idx_opportunities_ai_subtype
    ON opportunities(ai_subtype);

UPDATE schema_version SET version = 211;
