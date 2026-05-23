-- Schema v2.12: flag-as-bad-match feedback loop.
--
-- Adds:
--   * eval_decisions.flagged_at TEXT -- ISO 8601 UTC, set when a
--     user flags a decision as bad-match. NULL for normal evals
--     and for auto-skips at evaluator_version='rule-skip-v1'.
--   * flag_rules table -- patterns that auto-skip future
--     opportunities at persist time, saving cloud eval budget.
--
-- A rule matches an opportunity if every non-null pattern matches.
-- employer/title patterns use fnmatch wildcard semantics (* = any,
-- ? = single char), industry/function/ai_subtype patterns use
-- exact match. See engine/persistence/flag_rules.py for the
-- matcher dataclass.

ALTER TABLE eval_decisions ADD COLUMN flagged_at TEXT;

CREATE TABLE flag_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_pattern TEXT,
    title_pattern TEXT,
    industry_pattern TEXT,
    function_pattern TEXT,
    ai_subtype_pattern TEXT,
    source_opportunity_id INTEGER,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    FOREIGN KEY (source_opportunity_id) REFERENCES opportunities(id)
);

CREATE INDEX IF NOT EXISTS idx_flag_rules_active ON flag_rules(active);

UPDATE schema_version SET version = 212;
