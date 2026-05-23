-- Schema v2.20 migration from v2.19.
--
-- Spec JA-1 TASK 2 — adds four eval_decisions columns:
--   letter_grade (TEXT)       — A/B/C/D/F mapping of fit_score.
--   interview_plan (TEXT)     — JSON list of {topic, talking_point,
--                               proof_point} for A/B-graded postings.
--   red_flags (TEXT)          — JSON list of {flag, severity}.
--   culture_signals (TEXT)    — JSON list of {signal, sentiment} for
--                               A/B-graded postings.
--
-- All four are nullable; existing rows pre-Spec-JA-1 remain valid.
-- Idempotency: gated by `if current == 219:` in tracker._apply_migrations.

ALTER TABLE eval_decisions ADD COLUMN letter_grade TEXT;
ALTER TABLE eval_decisions ADD COLUMN interview_plan TEXT;
ALTER TABLE eval_decisions ADD COLUMN red_flags TEXT;
ALTER TABLE eval_decisions ADD COLUMN culture_signals TEXT;

UPDATE schema_version SET version = 220;
