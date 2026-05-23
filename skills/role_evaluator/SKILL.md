# Role evaluator skill

Single entry point for deciding whether a job posting matches a
candidate's career inventory. Wraps a deterministic Stage 2a filter
(hard exclusions, geography, seniority floor, completeness) and an
inventory-grounded Stage 2b LLM evaluator (gemma4:e4b by default,
fail-soft) into a single `RoleEvaluator.evaluate(posting)` call that
returns a tier, a match_score (0–100), and an audit trail. The eval
harness runs the whole pipeline against every posting and reports
precision @ top-K, recall on the human-labeled shortlist, and a
confusion matrix.

## When to use

- Pipeline orchestrator: `evaluator.evaluate(posting)` for each new
  posting; persist the result; sort by `match_score`.
- Eval harness / smoke runs: `python scripts/run_eval_harness.py
  --profile {id}` to score the whole tracker and produce a report.
- Ad-hoc CLI: import `RoleEvaluator` in a debug shell to spot-check
  a single posting end-to-end.

## When NOT to use

- Don't call individual stages (`Stage2a`, `Stage2b`, `combine`)
  directly from production code. They exist as composable parts but
  the contract is `RoleEvaluator.evaluate`. Going around it skips
  the fail-soft Stage 2b exception handling.
- Don't write to `stage_decisions` from inside `RoleEvaluator`. The
  evaluator is read-only against the DB; the eval harness (or the
  pipeline orchestrator) owns persistence.
- Don't try to make `evaluate()` cheaper by running Stage 2b in
  parallel across postings. The Ollama service is single-threaded
  on this hardware; concurrency just contends for the GPU.

## Public API

```python
from skills.role_evaluator import RoleEvaluator
from engine.persistence.tracker import Tracker
from llm.client import LLMClient

tracker = Tracker("default")
llm = LLMClient()
evaluator = RoleEvaluator("default", tracker, llm)

posting = tracker.get_opportunity_by_id(290)
result = evaluator.evaluate(posting)

print(result.combined_score.match_score)   # 0..100
print(result.combined_score.final_tier)    # TOP_TIER / STRONG / EXPLORATORY / SKIP
print(result.combined_score.reasoning)     # "[stage2a passed; 2b STRONG/high] ..."

# Stream + persist:
for r in evaluator.evaluate_batch(tracker.list_opportunities()):
    persist_evaluation_result(tracker, r)   # from eval_harness
```

| Method / property | Returns | When to call |
|---|---|---|
| `RoleEvaluator(profile_id, tracker, llm_client)` | – | Once per process. Builds InventoryTool, ProfileConfig, FewShotSelector, Stage2a, Stage2b. |
| `evaluate(posting)` | `EvaluationResult` | Per posting. Runs 2a, then 2b if 2a passed, then `combine`. Stage 2b exceptions are caught and converted to a SKIP verdict so combine() always sees valid input. |
| `evaluate_batch(postings)` | `Iterator[EvaluationResult]` | Stream over many postings. A `Stage 2a` exception on one posting logs and the iteration moves on (Stage 2b exceptions are caught one level deeper, in `evaluate`). |
| `version` | `str` | Identity for downstream `evaluator_version` columns. Currently `"2.0.0"`. |

`EvaluationResult` is a frozen dataclass with: `posting_id`,
`stage2a_verdict`, `stage2b_verdict` (None when 2a rejected),
`combined_score`, `evaluator_version`.

## Pipeline architecture

Three stages, evaluated in order:

```
posting -> Stage 2a (deterministic)
              |
              +-- REJECT_HARD       --> CombinedScore(0, SKIP, ...)
              +-- INSUFFICIENT_DATA --> CombinedScore(0, SKIP, ...)
              +-- PASS_TO_2B
                       |
                       v
                  Stage 2b (LLM)
                       |
                       +-- engagement_exclusion_match populated
                       |     --> CombinedScore(5, SKIP, ...)
                       |
                       +-- (tier, confidence) -> score table lookup
                             --> CombinedScore(score, tier, ...)
```

**Stage 2a rules in order:**
1. completeness — employer + title + posting_text required
2. hard_exclusion — substring-match employer against the union of
   `inventory_tool.get_hard_exclusions()` and
   `profile_config.config_hard_exclusions` (the parenthesized prefix
   of each exclusion is the match key, so engagement-type phrases
   like "Pure status-coordination roles" naturally don't match
   employer names)
3. geography — substring-match `posting.location` against
   `profile_config.geography`; null location passes when
   `remote_acceptable=True`
4. seniority_floor — parsed title seniority must be ≥ inventory's
   `current_level`
5. otherwise: `PASS_TO_2B`

**Stage 2b** renders a Jinja prompt with the inventory summary, the
engagement-type exclusions, the few-shot examples, and the posting,
then calls the LLM and parses a JSON verdict. Anti-fabrication rules
are stated explicitly. Unknown role IDs / cluster names returned by
the LLM are dropped (logged) before the verdict is finalized.

**`combine`** maps `(tier, confidence)` to a hand-picked score from
0–100. Engagement-type matches override to score 5 / SKIP regardless
of tier. Malformed verdicts (unknown tier or confidence) coerce to
EXPLORATORY/35 rather than crash.

## The evaluation contract

| `final_tier` | match_score range | Meaning |
|---|---|---|
| TOP_TIER | 78–92 | Inventory has direct, multi-role evidence for the posting's primary requirements. Worth a tailored application. |
| STRONG | 60–75 | Inventory matches the posting's core function with one or two adjacent gaps that can be bridged in a cover letter. |
| EXPLORATORY | 35–50 | Some transferable signal but real gaps. Worth applying only if pipeline volume permits. |
| SKIP | 0–10 | Inventory does not support the role; engagement-type match overrides; or 2a hard reject. |

`confidence` (low / medium / high) modifies the score within a tier
band. Tier alone is the primary signal; confidence flickers ±1 step
on borderline cases at temp=0.0 (see deferred items).

## Failure modes

- **Stage 2b parse failure** — the LLM returned non-JSON or invalid
  JSON. Stage 2b returns a SKIP/low verdict with
  `reasoning_text="parse_failure: <first 200 chars>"`. Combined
  score is 10. The eval harness counts these as `parse_failures`.
- **Ollama connection failure** — same fail-soft path with
  `reasoning_text="ollama_failure: <error>"`. Pipeline keeps moving;
  the failed postings can be re-evaluated by deleting the day's
  JSONL line and re-running.
- **`applicable_role_ids` empty** — the LLM produced human-readable
  labels instead of slug ids and the validator dropped them. Fix is
  in `skills/inventory/templates/summary.md.j2` exposing the slugs;
  if you change the template, re-render
  `data/{profile}/inventory_summary.md`.
- **Empty `EXPLORATORY` few-shot pool** — `FewShotSelector` warns
  once per process and the prompt renders 2 examples instead of 3.
  Verdict quality degrades on borderline EXPLORATORY cases. Fix is
  to label more postings or split shortlist labels into TOP_TIER /
  EXPLORATORY in `eval_labels`.
- **`InventoryTool` raises `FileNotFoundError`** — the profile's
  inventory hasn't been extracted, validated, or rendered. Run the
  4-step workflow in `skills/inventory/SKILL.md` Section 5.

## Eval harness usage

The harness scores every posting in the tracker, persists per-stage
records to `stage_decisions`, writes a JSONL trail (one line per
posting, flushed immediately) for inspection and resume, and
produces a markdown report.

**When to run:**
- After `career_inventory.md` has been re-extracted + summary
  re-rendered (the LLM-facing context changed).
- After `stage2b_prompt.txt` has been edited (the prompt changed).
- After Stage 2a rules change (different rejection set in the
  upstream filter).
- After the `(tier, confidence) -> score` table changes (different
  ranking).

**Expected wall clock on this hardware** (gemma4:e4b at temp=0.0,
4 GB VRAM, mixed CPU/GPU offload): ~150 seconds per posting that
reaches Stage 2b. Stage 2a-rejected postings are sub-second. Full
290-posting run lands around **8–12 hours**; a same-day --resume
makes a crashed run cheap to recover.

**Commands:**

```powershell
# Quick smoke (3 postings, ~8 minutes here):
python scripts\run_eval_harness.py --profile default --limit 3

# Full run (background recommended; ~8-12 hours):
python scripts\run_eval_harness.py --profile default

# Resume after a crash on the same day:
python scripts\run_eval_harness.py --profile default --resume
```

A same-day re-run without `--resume` errors out with `FileExistsError`
to prevent accidental overwrite of an in-progress JSONL. Delete the
day's JSONL or pass `--resume` to continue.

**Outputs (under `scripts/output/`):**

- `eval_verdicts_{YYYY-MM-DD}.jsonl` — one JSON line per posting,
  written and flushed after each Stage 2b call. Source of truth for
  resume; safe to inspect mid-run.
- `eval_report_{YYYYMMDDTHHMMSSZ}.md` — headline metrics, confusion
  matrix, per-cluster hit rate. New file per run.

**Headline metrics:**

- **precision @ top-K** — of the top-K postings by match_score, what
  fraction are labeled `shortlist` in `eval_labels`.
- **recall on shortlist** — of the shortlist-labeled postings, how
  many made the top-K.
- **confusion matrix** — predicted_tier × human_verdict over the
  labeled subset.
- **per_cluster_hit_rate** — how often each transferable cluster
  appears in `applicable_clusters` across the top-K. Sanity signal
  for whether the LLM is using the inventory's structure or just
  keyword-matching.
- **parse_failure_count** — Stage 2b verdicts that failed to parse
  the LLM output and fell through to SKIP/low. Fragility metric.

## Deferred items

- **Score table tuning** — the `(tier, confidence) -> score` map in
  `score.py` is hand-picked. Tuning is deferred until the eval
  harness gives empirical precision/recall numbers; we tune once,
  not iteratively.
- **`cultural_signals` / `red_flags`** — present in the inventory
  extract, omitted from the rendered summary, not consumed by the
  Stage 2b prompt. Surface them in the prompt only after the
  current evaluator establishes a baseline so we can measure the
  delta cleanly.
- **`eval_labels` schema standardization** — the table currently has
  only `verdict` (shortlist / skip / unsure) so the few-shot
  selector treats all `shortlist` rows as TOP_TIER and leaves the
  EXPLORATORY pool empty. A schema migration adding a tier column
  (or a labeling pass populating EXPLORATORY) would let the
  selector return all three calibration tiers.
- **Latency** — ~150s per Stage 2b call on the current hardware
  (gemma4:e4b at temp=0.0 on 4 GB VRAM, mixed CPU/GPU). A full eval
  over 290 postings runs in hours, not minutes. Fine for overnight
  batch; not interactive.
- **Cascade** — gemma4:e2b validation experiment (2026-05-02) showed
  E2B is ~9× faster but converges only 1–2 of 4 expected tiers and
  appears to ceiling at STRONG (never produces TOP_TIER). Cascade
  (E2B-then-E4B-on-uncertain) is currently parked; the eval-harness
  numbers from this commit are the baseline against which a future
  cascade attempt would be measured.
