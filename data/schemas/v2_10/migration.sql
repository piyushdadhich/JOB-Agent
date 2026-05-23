-- Schema v2.10: persisted resume + cover-letter prompts (Spec 3 --
-- Auto-prompt generation at evaluation time).
--
-- After a daily cloud-evaluator run, the run_batch_cloud orchestrator
-- pre-builds the resume + cover-letter prompts for every TOP_TIER /
-- STRONG posting and stamps them onto the application row. The
-- dashboard's Prompts panel reads these directly instead of building
-- them on demand, so the queue is populated before the user opens it.
--
-- Both columns are nullable + additive. No existing rows change.

ALTER TABLE applications ADD COLUMN resume_prompt       TEXT;
ALTER TABLE applications ADD COLUMN cover_letter_prompt TEXT;

UPDATE schema_version SET version = 210;
