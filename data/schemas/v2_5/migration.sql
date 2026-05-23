-- Schema v2.5: Eval set labels for Phase 6 measurement.
--
-- eval_labels stores the user's verdicts on a labeled set of
-- opportunities used to measure precision/recall of the
-- multi-stage evaluator pipeline.
--
-- Bootstrap: 30 labels from Phase 5d Step 5c shortlist_review.md
-- + 20 sampled extension labels = 50 case eval set.

CREATE TABLE eval_labels (
    opportunity_id  INTEGER PRIMARY KEY
                    REFERENCES opportunities(id),
    verdict         TEXT NOT NULL CHECK (verdict IN (
                        'shortlist', 'skip', 'unsure')),
    reason          TEXT,             -- short tag (role_mismatch, etc.)
    notes           TEXT,             -- free-form
    labeled_at      TEXT NOT NULL,
    labeled_by      TEXT NOT NULL DEFAULT 'default'
);

UPDATE schema_version SET version = 25;
