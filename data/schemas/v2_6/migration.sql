-- Schema v2.6: Stage decisions audit table for the multi-stage
-- evaluator pipeline (Phase 6).
--
-- Each opportunity gets one row per (stage_name, stage_version)
-- with the decision and reasoning. Pipeline orchestrator reads
-- prior stages from this table to short-circuit on
-- already-decided rows.
--
-- Migration is purely additive.

CREATE TABLE stage_decisions (
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    stage_name      TEXT NOT NULL,
    stage_version   TEXT NOT NULL,
    decision        TEXT NOT NULL,
    reason          TEXT,
    metadata        TEXT,            -- JSON blob for stage-specific data
    decided_at      TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, stage_name, stage_version)
);

CREATE INDEX idx_stage_decisions_decision
    ON stage_decisions(decision);
CREATE INDEX idx_stage_decisions_stage
    ON stage_decisions(stage_name, stage_version);

UPDATE schema_version SET version = 26;
