# Inventory skill

Structured, read-only access to a candidate's career inventory. The
skill loads a manually-curated `career_inventory.md`, returns it as
a validated `InventoryExtract` pydantic model, and renders a compact
markdown summary for LLM consumption. Callers: Stage 2a (hard
exclusions), Stage 2b (LLM context), pipeline orchestrator (staleness
check at start of run), ad-hoc CLI. Critical constraint: this skill
**never** extracts. Extraction is a manual workflow run by the user
in a Claude Code session — see Section 5.

## When to use

- Stage 2a: filter postings against `hard_exclusions`.
- Stage 2b: pass `get_summary()` into the LLM prompt as candidate
  context.
- Pipeline orchestrator: call `is_stale()` at the start of a run; if
  True, halt and tell the user to re-extract.
- Resume generator (future): pull role-by-role detail via
  `get_role(role_id)`.
- Tests / debug shells: cheap, in-process access to a profile's
  inventory without touching disk paths.

## When NOT to use

- Don't write to inventory files through this skill. It is read-only.
  `reload()` is the only mutator and only re-reads from disk.
- Don't call `InventoryTool` from inside a Jinja or LLM prompt
  template. Resolve `get_summary()` / `get_extract()` in Python and
  pass the resulting strings/objects in as context.
- Don't try to trigger extraction programmatically. Extraction is
  manual by design (Section 6); there is no `extract()` method.

## Public API

```python
from skills.inventory import InventoryTool

tool = InventoryTool("default")           # raises if profile/extract missing

if tool.is_stale():
    print(tool.staleness_reason())
    # caller runs the manual extraction workflow (Section 5),
    # then:
    tool.reload()

summary    = tool.get_summary()                        # str (markdown)
extract    = tool.get_extract()                        # InventoryExtract
role       = tool.get_role("testco_payments_2025")  # Role | None
exclusions = tool.get_hard_exclusions()                # list[str]
clusters   = tool.get_transferable_clusters()          # list[TransferableSkillCluster]

print(tool.profile_id, tool.source_hash[:8], tool.extracted_at)
```

| Method | Returns | Raises | When to call |
|---|---|---|---|
| `InventoryTool(profile_id)` | – | `FileNotFoundError` if `data/{id}/` or `inventory_extract.json` missing; `ValidationError` if schema breaks | Once per process / per profile. Eager-loads extract; lazy-loads summary. |
| `is_stale()` | `bool` | – | At pipeline start, or before any work that depends on freshness. |
| `staleness_reason()` | `str \| None` | – | When you want to log *why* something is stale; `None` when fresh. |
| `get_extract()` | `InventoryExtract` | – | When you need typed access to roles, trajectory, etc. |
| `get_summary()` | `str` | `FileNotFoundError` if summary not rendered | LLM context for Stage 2b. Run `render_summary.py` first. |
| `get_role(role_id)` | `Role \| None` | – | Look up one role by id; `None` is a normal "not found." |
| `get_hard_exclusions()` | `list[str]` | – | Stage 2a employer-name filter. |
| `get_transferable_clusters()` | `list[TransferableSkillCluster]` | – | Cluster-level evidence for evaluation prompts. |
| `get_extraction_prompt()` | `str` | `FileNotFoundError` if `career_inventory.md` missing | Programmatic access to the assembled prompt; mirrors the CLI. |
| `reload()` | `None` | `FileNotFoundError` if extract gone | After the user has finished a manual extraction + validate. |
| `profile_id` | `str` | – | Identity. |
| `source_hash` | `str` (64-char hex) | – | The hash of `career_inventory.md` that produced this extract. |
| `extracted_at` | `datetime` | – | When the extract was generated. Useful for log lines. |

Read-only contract: the only state-changing method is `reload()`,
which re-reads `inventory_extract.json` and clears the cached summary.

## Data shape

`skills/inventory/schema.py` is the source of truth. The top-level
shape:

```
InventoryExtract
├── schema_version: int
├── profile_id: str
├── source_hash: str                 # sha256(career_inventory.md)
├── extracted_at: str (ISO 8601)
├── extractor_model: str
├── roles: list[Role]
│    ├── id, employer, title         # required
│    ├── start_date, function        # required
│    ├── seniority_level             # junior | mid | senior | lead | manager | director | vp
│    ├── end_date: str | None        # None means "Present"
│    ├── location: str | None        # None means inventory didn't state one
│    ├── industry: str | None        # None for Independent / cross-industry roles
│    ├── skill_clusters: list[str]
│    ├── evidence_phrases: list[str]
│    ├── outcomes: list[str]
│    ├── cultural_signals: list[str]
│    └── red_flags: list[str]
├── transferable_skill_clusters: list[TransferableSkillCluster]
│    ├── name, summary
│    └── evidence_role_ids: list[str]   # must reference real role.id
├── trajectory: Trajectory
│    ├── current_level, advance_step, lateral_step
│    └── target_levels, avoid: list[str]
├── hard_exclusions: list[str]
└── geography: list[str]
```

Nullable-field rationale: `end_date` is null for the current role
(rendered as "Present" in the summary). `location` and `industry` are
null for roles where the inventory legitimately doesn't state one
(e.g., the Independent Consultant role has neither).

The rendered summary (`get_summary()`) deliberately omits
`evidence_phrases`, `cultural_signals`, and `red_flags` — they are
preserved in the extract for audit but excluded from the LLM-facing
digest until Stage 2b proves it needs them.

## Extraction workflow

Inventory extraction is a **manual** five-step loop. Run it whenever
`is_stale()` returns True, or whenever you've edited
`career_inventory.md`.

1. **Edit the source.** Update
   `source_materials/{profile}/career_inventory.md` with the new
   role / outcome / trajectory content.

2. **Assemble the prompt.**
   ```powershell
   python skills\inventory\get_extraction_prompt.py --profile default
   ```
   Reads the inventory + `prompts/extraction_prompt.txt`, substitutes
   placeholders, copies the full prompt to the clipboard, and writes
   `data/{profile}/.last_extraction_prompt.txt` for re-use.

3. **Run extraction in Claude Code.** Open a fresh Claude Code session
   (or claude.ai), paste the prompt, wait for the JSON response, and
   save it verbatim to:
   ```
   data/{profile}/inventory_extract.json
   ```

4. **Validate and archive.**
   ```powershell
   python skills\inventory\validate_extract.py --profile default
   ```
   Strips any leftover fences/thinking blocks, runs the pydantic
   schema, verifies `source_hash` against the current
   `career_inventory.md`, archives the prior extract under
   `inventory_history/`, and writes a structural diff.

5. **Render the summary.**
   ```powershell
   python skills\inventory\render_summary.py --profile default
   ```
   Produces `data/{profile}/inventory_summary.md` — the markdown
   digest that Stage 2b consumes.

After step 5, in-process callers should call `tool.reload()`. Long-
running processes (the orchestrator, an interactive shell) won't see
the new extract until reload, even though the file on disk has
changed.

## Why extraction is manual (architectural decision #36)

Two attempts to run inventory extraction locally on Gemma 4 E4B
fabricated content past the ~3 KB output mark on the user's hardware
(GTX 1050 Ti, 4 GB VRAM, mixed CPU/GPU offload):

- **Run 1** invented a non-existent "Master's in IT" degree on the
  output side that did not appear anywhere in the source inventory.
- **Run 2** entered a degenerate loop, repeating "not explicitly
  defined" until `num_predict` exhausted, producing no usable JSON.

Both failure transcripts are preserved at
`data/{profile}/inventory_history/failed_*_gemma4-e4b-local.txt`.

The diagnosis was attention degradation under VRAM pressure: the
~50 KB inventory plus structured-output constraints exceeded what
the model could keep coherent on this hardware. Local Gemma 4 E4B
remains fine for **frequent, small** operations (Stage 2b posting
evaluation runs at a few KB at a time and is well within budget).
It is unsuitable for **large, structurally faithful** extraction.

Manual Claude Code extraction is acceptable because the inventory
changes a few times per year, not per day. The tradeoff (a few
minutes of human time, a few times a year) buys structural fidelity
that we can actually trust.

## Failure modes

- **`FileNotFoundError: Profile directory not found`** — `data/{id}/`
  doesn't exist. Either the profile id is wrong or the user hasn't
  set up this profile yet.
- **`FileNotFoundError: Inventory extract not found`** — extract file
  missing. Run the extraction workflow (Section 5).
- **`FileNotFoundError: Summary not found`** — extract is loaded but
  `inventory_summary.md` hasn't been rendered. Run
  `render_summary.py`.
- **Schema validation failure** — most often a field that's nullable
  in reality but required in the schema. We hit this with `location`
  (Independent Consultant) and `industry` (cross-industry roles); the
  fix is in `schema.py` `Optional[]` coverage. If validation fails on
  a fresh extract, check there before re-running Claude.
- **`is_stale()` returns True** — `career_inventory.md` content has
  changed since the extract was generated. Caller must run the manual
  workflow; the skill never auto-extracts.

## Deferred items

- **`jinja2` pin** — currently unpinned. See `TODO(deps)` in
  `tool.py`. Lands when the repo introduces a project-wide
  `requirements.txt` or `pyproject.toml`.
- **`cultural_signals` / `red_flags`** — extracted but excluded from
  the rendered summary. Decision deferred to Component 2 (Stage 2b);
  if the evaluator can use them, surface them in the template then.
- **Summary size** — the rendered `inventory_summary.md` is ~9.7 KB
  on the user's data, larger than the original ~5 KB target. Acceptable
  for now; revisit if the Stage 2b context budget gets tight (lever:
  drop the outcomes cap from 3 → 2, or drop the evidence-role-ids
  line from clusters).
