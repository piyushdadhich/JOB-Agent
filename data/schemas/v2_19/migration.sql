-- Schema v2.19 migration from v2.18.
--
-- Spec H1 (revised) — adds personal_site_url to persons. The revised
-- TASK 1 spec specified this column on the persons table; the initial
-- v2.18 migration missed it, so v2.19 patches it in.
--
-- Idempotency: gated by the version check in tracker._apply_migrations
-- (current == 218).

ALTER TABLE persons ADD COLUMN personal_site_url TEXT;

UPDATE schema_version SET version = 219;
