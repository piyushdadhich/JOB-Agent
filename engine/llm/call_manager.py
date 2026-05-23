"""Plans and executes cloud evaluation within RPD budget.

Phases:
1. INVENTORY — count work items + budget (zero API calls)
2. PLAN — decide what runs today (zero API calls)
3. EXECUTE — run the plan (API calls happen here)
4. REPORT — summary for log + stdout (zero API calls)

No API call fires until the manager has planned the full batch.

CallManager is deliberately decoupled from persistence and from the
project's posting-discovery query. The pipeline integration in
skills/role_evaluator/cloud_pipeline.py wires it to the real tracker
and persist_verdict.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .gemma_cloud_client import (
    GemmaCloudClient,
    GemmaCloudError,
    RateLimitedError,
    ServerError,
)

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "gemma4-cloud-v1"


class CallManager:
    RPD_LIMIT = 1500
    SOFT_STOP = 1200
    FAST_EVAL_RESERVE = 100
    DEBUG_RESERVE = 100
    MAX_RETRIES_PER_POSTING = 2

    def __init__(
        self,
        profile_id: str,
        gemma_client: GemmaCloudClient,
        find_postings_fn: Callable[[], list[dict]],
        get_posting_fn: Callable[[int], Optional[dict]],
        persist_fn: Callable[[dict, dict], None],
        prompt_template: str,
        inventory_summary: str,
        on_cloud_5xx: Optional[
            Callable[[dict, Exception], None]
        ] = None,
    ):
        self.profile_id = profile_id
        self.client = gemma_client
        self._find_postings = find_postings_fn
        self._get_posting = get_posting_fn
        self._persist = persist_fn
        self._prompt_template = prompt_template
        self._inventory_summary = inventory_summary
        self._on_cloud_5xx = on_cloud_5xx
        self.queue_path = Path(
            f"data/{profile_id}/cloud_eval_queue.json"
        )
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: INVENTORY ─────────────────────────────────

    def inventory(self) -> dict:
        """Count work items + budget. Zero API calls.

        Materializes retry queue entries into posting-shaped dicts
        via get_posting_fn so plan() and execute() see a uniform
        list. Queue entries whose posting no longer exists in the
        tracker are dropped from the queue.
        """
        used = self.client.count_today()
        available = max(0, self.SOFT_STOP - used)
        new_postings = list(self._find_postings())

        retry_postings: list[dict] = []
        abandoned_ids: list[int] = []
        for entry in self._load_retry_queue():
            pid = entry["opportunity_id"]
            posting = self._get_posting(pid)
            if posting is None:
                abandoned_ids.append(pid)
                logger.warning(
                    "Retry posting %s no longer in tracker; dropping",
                    pid,
                )
                continue
            posting = dict(posting)
            posting["_queued_at"] = entry.get("queued_at") or ""
            retry_postings.append(posting)
        if abandoned_ids:
            self._drop_from_queue(abandoned_ids)

        return {
            "budget": {
                "rpd_limit": self.RPD_LIMIT,
                "soft_stop": self.SOFT_STOP,
                "used_today": used,
                "available": available,
            },
            "work": {
                "new_postings": new_postings,
                "new_count": len(new_postings),
                "retry_queue": retry_postings,
                "retry_count": len(retry_postings),
                "total_demand": len(new_postings) + len(retry_postings),
            },
        }

    # ── Phase 2: PLAN ──────────────────────────────────────

    def plan(self, inv: dict) -> dict:
        """Decide what runs today. Zero API calls.

        Priority: new postings (newest first) > retries (oldest first).
        Excess new postings auto-surface tomorrow via find_postings_fn.
        """
        available = inv["budget"]["available"]

        if available <= 0:
            return {
                "scenario": "NO_BUDGET",
                "eval_batch": [],
                "eval_count": 0,
                "deferred_new": inv["work"]["new_count"],
                "deferred_retries": inv["work"]["retry_count"],
                "estimated_minutes": 0,
                "budget_after": 0,
            }

        new_sorted = sorted(
            inv["work"]["new_postings"],
            key=lambda p: p.get("date_discovered") or "",
            reverse=True,
        )
        new_batch = new_sorted[:available]
        deferred_new = max(0, len(new_sorted) - available)
        budget_after_new = available - len(new_batch)

        retry_sorted = sorted(
            inv["work"]["retry_queue"],
            key=lambda r: r.get("_queued_at") or "",
        )
        retry_batch = retry_sorted[:budget_after_new]
        deferred_retries = max(0, len(retry_sorted) - budget_after_new)

        eval_batch = new_batch + retry_batch
        eval_count = len(eval_batch)
        budget_after = available - eval_count

        scenario = (
            "FULL_RUN"
            if deferred_new == 0 and deferred_retries == 0
            else "PARTIAL_RUN"
        )

        return {
            "scenario": scenario,
            "eval_batch": eval_batch,
            "eval_count": eval_count,
            "deferred_new": deferred_new,
            "deferred_retries": deferred_retries,
            "estimated_minutes": round(eval_count / 15) + 1,
            "budget_after": budget_after,
        }

    # ── Phase 3: EXECUTE ───────────────────────────────────

    def execute(self, plan_dict: dict, auto: bool = True) -> dict:
        """Optionally confirm, then run the plan. API calls here.

        On failure per posting:
        - 429: exponential backoff, max 3 attempts (counts toward budget)
        - 500/503/parse error: add to retry queue, move to next
        - After MAX_RETRIES_PER_POSTING failures: status = abandoned
        """
        if plan_dict["eval_count"] == 0:
            return self._empty_results()

        self._print_plan(plan_dict)

        if not auto:
            confirm = input("Proceed? [Y/n] ").strip().lower()
            if confirm and confirm != "y":
                print("Aborted by user.")
                return self._empty_results()

        results = self._empty_results()
        for i, item in enumerate(plan_dict["eval_batch"]):
            if self.client.count_today() >= self.SOFT_STOP:
                logger.warning(
                    "Soft stop reached at item %d/%d. "
                    "Remaining items auto-surface tomorrow.",
                    i, plan_dict["eval_count"],
                )
                break

            if i > 0 and i % 50 == 0:
                remaining = self.SOFT_STOP - self.client.count_today()
                print(
                    f"  [{i}/{plan_dict['eval_count']}] "
                    f"budget: {remaining} remaining | "
                    f"errors: {results['failed']}"
                )

            try:
                parsed = self._evaluate_with_retry(item)
                self._persist(item, parsed)
                results["evaluated"] += 1
                if parsed["verdict"] == "SKIP":
                    results["skipped_by_model"] += 1
                else:
                    results["proceeded"] += 1
                self._mark_retry_completed(item["id"])

            except ServerError as e:
                # 500/503: do NOT count as failure and do NOT queue.
                # If a fallback callback is wired (cloud_pipeline
                # two-stage path), hand off to it; otherwise legacy
                # behavior (queue for retry).
                if self._on_cloud_5xx is not None:
                    try:
                        self._on_cloud_5xx(item, e)
                        results["fallback_to_local"] += 1
                        self._mark_retry_completed(item["id"])
                    except Exception as fallback_err:
                        logger.exception(
                            "on_cloud_5xx fallback failed for %s; "
                            "queueing", item["id"],
                        )
                        results["failed"] += 1
                        self._add_to_retry_queue(
                            item, str(fallback_err),
                        )
                else:
                    results["failed"] += 1
                    self._add_to_retry_queue(item, str(e))

            except GemmaCloudError as e:
                results["failed"] += 1
                self._add_to_retry_queue(item, str(e))

        return results

    def _evaluate_with_retry(self, item: dict) -> dict:
        """Try the API call. Retry only on 429. Max 3 attempts."""
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                return self.client.evaluate_posting(
                    opportunity_id=item["id"],
                    employer=item.get("employer", ""),
                    title=item.get("title", ""),
                    location=item.get("location", ""),
                    posting_text=item.get("posting_text") or "",
                    inventory_summary=self._inventory_summary,
                    prompt_template=self._prompt_template,
                )
            except RateLimitedError:
                if attempt < max_attempts - 1:
                    wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.info(
                        "429 — backoff %ds (attempt %d/%d)",
                        wait, attempt + 1, max_attempts,
                    )
                    time.sleep(wait)
                else:
                    raise
            except (ServerError, GemmaCloudError):
                raise

    # ── Phase 4: REPORT ────────────────────────────────────

    def report(self, results: dict, plan_dict: dict) -> str:
        used = self.client.count_today()
        return (
            "\n--- CLOUD EVALUATOR REPORT ---\n"
            f"  Model:              {GemmaCloudClient.MODEL}\n"
            f"  Evaluated:          {results['evaluated']} postings\n"
            f"    Proceeded:        {results['proceeded']}\n"
            f"    Skipped by model: {results['skipped_by_model']}\n"
            f"  Failed (queued):    {results['failed']}\n"
            f"  Budget used today:  {used} / {self.SOFT_STOP} (soft limit)\n"
            f"  Budget remaining:   {self.SOFT_STOP - used}\n"
            f"  RPD total:          {self.RPD_LIMIT}\n"
            f"  Deferred to tmrw:   "
            f"{plan_dict.get('deferred_new', 0)} new + "
            f"{plan_dict.get('deferred_retries', 0)} retry\n"
            f"  Retry queue depth:  {len(self._load_retry_queue())}\n"
        )

    # ── Internals ──────────────────────────────────────────

    def _empty_results(self) -> dict:
        return {
            "evaluated": 0,
            "skipped_by_model": 0,
            "proceeded": 0,
            "failed": 0,
            "fallback_to_local": 0,
        }

    def _print_plan(self, plan_dict: dict) -> None:
        print("\n--- CLOUD EVALUATOR PLAN ---")
        print(f"  Scenario:               {plan_dict['scenario']}")
        if "priority_1_count" in plan_dict:
            p1 = plan_dict["priority_1_count"]
            p2 = plan_dict.get("priority_2_count_post_prefilter", 0)
            prefilter = plan_dict.get("prefilter_skipped", 0)
            print(
                f"  Priority 1 (AI direct): {p1}  "
                "(-> cloud, fallback to local on 5xx)"
            )
            print(
                f"  Priority 2 (pre-filter): {p2}  "
                "(-> cloud, fallback to local on 5xx)"
            )
            print(f"  Pre-filter skipped:     {prefilter}")
        print(f"  To evaluate:            {plan_dict['eval_count']}")
        print(f"  Estimated minutes:      {plan_dict['estimated_minutes']}")
        print(f"  Budget after:           {plan_dict['budget_after']}")
        if plan_dict.get("deferred_new"):
            print(
                f"  Deferred (new):         {plan_dict['deferred_new']} "
                "(auto-surface tomorrow)"
            )
        if plan_dict.get("deferred_retries"):
            print(
                f"  Deferred (retry):       {plan_dict['deferred_retries']}"
            )

    # --- retry queue ------------------------------------------------

    def _load_retry_queue_all(self) -> list[dict]:
        if not self.queue_path.exists():
            return []
        with open(self.queue_path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []

    def _load_retry_queue(self) -> list[dict]:
        return [
            item for item in self._load_retry_queue_all()
            if item.get("status") == "queued"
            and item.get("retry_count", 0) < self.MAX_RETRIES_PER_POSTING
        ]

    def _save_retry_queue(self, queue: list[dict]) -> None:
        with open(self.queue_path, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)

    def _drop_from_queue(self, ids: list[int]) -> None:
        queue = self._load_retry_queue_all()
        ids_set = set(ids)
        new_queue = [
            q for q in queue if q.get("opportunity_id") not in ids_set
        ]
        if len(new_queue) != len(queue):
            self._save_retry_queue(new_queue)

    def _add_to_retry_queue(self, item: dict, error: str) -> None:
        queue = self._load_retry_queue_all()
        existing = next(
            (q for q in queue if q.get("opportunity_id") == item["id"]),
            None,
        )
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            existing["retry_count"] = existing.get("retry_count", 0) + 1
            existing["last_attempt_at"] = now
            existing["failure_reason"] = error[:200]
            if existing["retry_count"] >= self.MAX_RETRIES_PER_POSTING:
                existing["status"] = "abandoned"
                logger.warning(
                    "Posting %s abandoned after %d retries: %s",
                    item["id"], existing["retry_count"], error[:100],
                )
        else:
            queue.append({
                "opportunity_id": item["id"],
                "employer": item.get("employer", ""),
                "title": item.get("title", ""),
                "queued_at": now,
                "failure_reason": error[:200],
                "retry_count": 0,
                "last_attempt_at": now,
                "status": "queued",
            })
        self._save_retry_queue(queue)

    def _mark_retry_completed(self, opportunity_id: int) -> None:
        queue = self._load_retry_queue_all()
        new_queue = [
            q for q in queue
            if q.get("opportunity_id") != opportunity_id
        ]
        if len(new_queue) != len(queue):
            self._save_retry_queue(new_queue)
