"""Adapter from OpportunityRecord (discovery layer) to Tracker (persistence).

Discovery sources produce OpportunityRecord objects; persisting them is
upsert_company + insert_opportunity + (v2.11) classification stamp +
(v2.12) flag_rule auto-skip check. This module is the only place that
bridge lives, so sources stay free of SQL and the tracker stays free
of dataclass knowledge.
"""

from __future__ import annotations

import json
from datetime import datetime, date, timezone

from engine.discovery.base import OpportunityRecord
from engine.persistence.classifiers import (
    classify_ai_subtype,
    classify_city,
    derive_eval_priority,
)
from engine.persistence.flag_rules import rule_from_row
from engine.persistence.tracker import (
    Tracker,
    normalize_employer_name,
)

__all__ = [
    "persist_record",
    "normalize_employer_name",
    "IncompleteRecordError",
]


class IncompleteRecordError(ValueError):
    """Raised when an OpportunityRecord lacks fields required for persist
    (employer or source_url or title). Distinct from TrackerError so
    callers can choose to log-and-continue rather than abort."""


def _isoformat_or_passthrough(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def persist_record(
    tracker: Tracker, record: OpportunityRecord
) -> tuple[int, bool]:
    """Persist an OpportunityRecord via tracker.

    Raises IncompleteRecordError if record.employer, record.source_url,
    or record.title is empty/None — these are NOT NULL in the schema and
    upsert_company explicitly rejects empty names.
    """
    if not record.employer or not record.employer.strip():
        raise IncompleteRecordError(
            f"record missing employer (source={record.source}, "
            f"url={record.source_url})"
        )
    if not record.source_url or not record.source_url.strip():
        raise IncompleteRecordError(
            f"record missing source_url (source={record.source}, "
            f"employer={record.employer})"
        )
    if not record.title or not record.title.strip():
        raise IncompleteRecordError(
            f"record missing title (source={record.source}, "
            f"url={record.source_url})"
        )

    company_id = tracker.upsert_company(
        name=record.employer,
        industry=record.employer_industry,
    )
    opp_id, was_new = tracker.insert_opportunity(
        company_id=company_id,
        source=record.source,
        source_url=record.source_url,
        title=record.title,
        source_id=record.source_id,
        location=record.location,
        posting_text=record.posting_text,
        is_remote=record.is_remote,
        posted_at=_isoformat_or_passthrough(record.posted_at),
        salary_min=record.salary_min,
        salary_max=record.salary_max,
        salary_currency=record.salary_currency,
        salary_interval=record.salary_interval,
        raw_payload=record.raw_payload,
        search_context=record.search_context,
    )

    # v2.11 classification stamp (city / ai_subtype / eval_priority).
    # function + industry_normalized stay null at insert; only the
    # v2.11 backfill of existing eval_decisions populates them today.
    city = classify_city(record.location)
    ai_subtype = classify_ai_subtype(record.title, record.search_context)
    tracker.update_opportunity_classification(
        opp_id,
        city=city,
        ai_subtype=ai_subtype,
        eval_priority=derive_eval_priority(ai_subtype),
    )

    # v2.12 flag-rule check: any active rule matching this fresh
    # opportunity gets an immediate eval_decision at 'rule-skip-v1',
    # so it never reaches Stage 2pre / cloud. Only runs on inserts
    # (was_new=True) to avoid re-skipping already-evaluated rows.
    if was_new:
        _check_flag_rules_at_persist(
            tracker, opp_id, record, ai_subtype=ai_subtype,
        )

    # v2.14 hiring-team persistence. Source modules (currently only
    # linkedin_guest) put a list[dict] into raw_payload["hiring_team"];
    # other sources omit it. Each member becomes a job_posters row +
    # an opportunity_posters link. The full list is also serialized
    # into opportunities.hiring_team_json for easy single-row lookup.
    _persist_hiring_team(tracker, opp_id, record)

    return opp_id, was_new


def _persist_hiring_team(
    tracker: Tracker, opp_id: int, record: OpportunityRecord,
) -> None:
    """Write job_posters + opportunity_posters rows for any hiring-team
    members surfaced by the source, and serialize the full list onto
    opportunities.hiring_team_json. Silent no-op when the source did
    not include a hiring team."""
    hiring_team = (record.raw_payload or {}).get("hiring_team") or []
    if not hiring_team:
        return
    observed_at = _isoformat_or_passthrough(record.date_discovered)
    if observed_at is None:
        observed_at = datetime.now(timezone.utc).isoformat()
    for member in hiring_team:
        name = (member.get("name") or "").strip()
        if not name:
            continue
        poster_id = tracker.upsert_job_poster(
            name=name,
            title=member.get("title"),
            profile_url=member.get("profile_url"),
            employer=record.employer,
            observed_at=observed_at,
        )
        tracker.link_opportunity_to_poster(
            opportunity_id=opp_id,
            job_poster_id=poster_id,
            role_on_posting=member.get("role") or "hiring_team",
            observed_at=observed_at,
        )
    tracker._execute(
        "UPDATE opportunities SET hiring_team_json = ? WHERE id = ?",
        (json.dumps(hiring_team), opp_id),
    )


def _check_flag_rules_at_persist(
    tracker: Tracker,
    opp_id: int,
    record: OpportunityRecord,
    *,
    ai_subtype,
) -> None:
    """Walk active flag_rules; on first match, write a rule-skip-v1
    eval_decision and stop.

    function + industry_normalized are NULL at insert time (they
    populate via the v2.11 reasoning-JSON backfill), so rules using
    those pattern fields won't fire here — they only kick in once
    those columns get values.
    """
    for row in tracker.list_flag_rules(active_only=True):
        rule = rule_from_row(row)
        if not rule.has_any_pattern():
            continue
        if rule.matches(
            employer=record.employer or "",
            title=record.title or "",
            industry=None,
            function=None,
            ai_subtype=ai_subtype,
        ):
            now = datetime.now(timezone.utc).isoformat()
            tracker.record_evaluation(
                opportunity_id=opp_id,
                evaluator_version="rule-skip-v1",
                tier="SKIP",
                fit_score=None,
                sector=None, role_type=None,
                stage_trace={"matched_rule_id": row["id"]},
                reasoning=(
                    f"matched flag_rule_id={row['id']}; "
                    "auto-skipped at persist time"
                ),
            )
            return
