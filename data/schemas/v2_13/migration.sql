-- Schema v2.13: drop dead-but-present columns.
--
-- companies.size_label, opportunities.notes, opportunities.tags
-- were 0% populated for business-logic consumers per Spec 6a TASK 6.0
-- audit (May 12, 2026). All writers and readers removed in commits
-- a247f69 / 3bb923b / a803c5b before this column drop.
--
-- SQLite 3.35+ supports ALTER TABLE DROP COLUMN natively (Python 3.12
-- ships 3.49+); no table-recreate fallback needed.

ALTER TABLE opportunities DROP COLUMN notes;
ALTER TABLE opportunities DROP COLUMN tags;
ALTER TABLE companies     DROP COLUMN size_label;

UPDATE schema_version SET version = 213;
