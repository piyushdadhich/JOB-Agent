-- Schema v2.7: ATS detection columns on companies (Phase 5a Step 6).
--
-- Adds 6 nullable columns to support the ATS Detector:
--   canonical_domain          — best-effort domain guess
--   ats_platform              — greenhouse, lever, ashby, workable,
--                               personio, or recruitee (validated by
--                               the writer; no CHECK because SQLite
--                               can't ALTER TABLE add CHECK)
--   ats_slug                  — CASE-SENSITIVE; never lowercased
--   ats_detected_at           — ISO 8601 UTC
--   ats_detection_method      — manual_override | careers_page |
--                               slug_probe
--   ats_detection_confidence  — 0.0–1.0
--
-- Migration is purely additive: all columns are nullable, no existing
-- rows change.

ALTER TABLE companies ADD COLUMN canonical_domain         TEXT;
ALTER TABLE companies ADD COLUMN ats_platform             TEXT;
ALTER TABLE companies ADD COLUMN ats_slug                 TEXT;
ALTER TABLE companies ADD COLUMN ats_detected_at          TEXT;
ALTER TABLE companies ADD COLUMN ats_detection_method     TEXT;
ALTER TABLE companies ADD COLUMN ats_detection_confidence REAL;

CREATE INDEX IF NOT EXISTS idx_companies_ats_platform
    ON companies(ats_platform);

UPDATE schema_version SET version = 27;
