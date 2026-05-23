-- Schema v2.9: applications table expansion (Phase 11 -- Playwright
-- Application Agent + Dashboard).
--
-- Adds 9 nullable columns to applications so the agent can persist:
--   resume_text         -- full text of the submitted resume
--   cover_letter_text   -- full text of the submitted cover letter
--   ats_platform        -- which handler ran the submission
--   screenshot_path     -- confirmation page screenshot (relative path)
--   submitted_url       -- final URL after redirects (post-submit)
--   screening_answers   -- JSON dict of question -> answer for the
--                         screening questions filled this submission
--   selected_at         -- dashboard: user clicked Apply on Shortlist
--   prompt_generated_at -- dashboard: prompts displayed in Prompts tab
--   docs_ready_at       -- dashboard: both resume + CL text saved
--
-- All columns are nullable + additive. No existing rows change.

ALTER TABLE applications ADD COLUMN resume_text         TEXT;
ALTER TABLE applications ADD COLUMN cover_letter_text   TEXT;
ALTER TABLE applications ADD COLUMN ats_platform        TEXT;
ALTER TABLE applications ADD COLUMN screenshot_path     TEXT;
ALTER TABLE applications ADD COLUMN submitted_url       TEXT;
ALTER TABLE applications ADD COLUMN screening_answers   TEXT;
ALTER TABLE applications ADD COLUMN selected_at         TEXT;
ALTER TABLE applications ADD COLUMN prompt_generated_at TEXT;
ALTER TABLE applications ADD COLUMN docs_ready_at       TEXT;

CREATE INDEX IF NOT EXISTS idx_applications_ats_platform
    ON applications(ats_platform);

UPDATE schema_version SET version = 29;
