-- Schema v2.3: Hybrid taxonomy storage.
-- Adds two columns to opportunities so we can persist both
-- Lightcast (primary, consumed by scorer) and ESCO (secondary,
-- annotation only -- for human-facing display and future
-- Gemma-judge analysis).
-- Migration is purely additive. No data is rewritten.

ALTER TABLE opportunities ADD COLUMN extracted_skill_ids_secondary TEXT;
-- JSON array of secondary-taxonomy skill IDs. Annotation only.
-- The three-signal scorer (Phase 5d Step 5) reads
-- extracted_skill_ids ONLY (the primary column).

ALTER TABLE opportunities ADD COLUMN secondary_taxonomy TEXT;
-- Records which taxonomy is in the secondary column.
-- Currently 'esco' when primary is 'lightcast'. Stored per-row so
-- that future re-runs with different taxonomy choices remain
-- self-describing.

UPDATE schema_version SET version = 23;
