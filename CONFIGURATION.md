# Configuration reference

Every config file ships with a `.example` sibling. The setup wizard writes the live versions on first run; this doc is the reference if you ever need to hand-edit.

## File map

| File | Purpose | Tracked? | Required? |
|------|---------|----------|-----------|
| `config/profiles/default.yaml.example` | Profile template | yes | — |
| `config/profiles/default.yaml` | Active profile (wizard writes) | no (gitignored) | yes |
| `config/profiles/default_applicant.yaml.example` | Applicant-PII template | yes | — |
| `config/profiles/default_applicant.yaml` | Form-filler PII | no (gitignored) | only if using the form-filler |
| `config/domains/corporate.yaml.example` | Domain vocabulary template | yes | — |
| `config/domains/corporate.yaml` | Active domain vocabulary | no (gitignored) | yes |
| `config/title_exclusions.yaml` | Title pre-filter | yes (template) | optional |
| `config/red_flag_phrases.yaml` | Posting-text warning phrases | yes (template) | optional |
| `config/applicant_qa_patterns.yaml` | Form-filler Q&A patterns | yes (template) | optional |
| `.env` | API keys | no (gitignored) | only if using cloud LLMs |

## `default.yaml` — profile config

### Identity

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `profile_id` | string | `default` | Slug used in `data/{profile_id}/` paths |
| `display_name` | string | `Your Name` | Shown in the dashboard header |
| `domain` | string | `corporate` | Selects `config/domains/<domain>.yaml` |

### Day boundary

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `agent.day_boundary_hour` | int (0-23) | `3` | Hour at which "today" rolls over in local time. Useful for aligning with a cloud-LLM quota reset. |

### Target market

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `target_cities` | list[string] | `[your_city]` | City slugs the discovery agent searches. `remote_<country>` is a special slug matching any remote posting in that country. |
| `target_role_types` | list[string] | `[]` | Keys must exist in `config/domains/<domain>.yaml` |
| `target_sectors_by_city` | dict | `{}` | Per-city sector list. Skipped if empty. |
| `salary.floor` / `target` / `cap` | int | — | Used to flag postings below floor and to set salary expectation in form-filler |
| `employer_size_priority` | string | `mid_sized` | One of `small`, `mid_sized`, `large`, `enterprise` |

<details>
<summary>Example</summary>

```yaml
target_cities:
  - your_city
  - remote_<your_country>

target_role_types:
  - your_role_type
  - another_role_type

target_sectors_by_city:
  your_city: [your_sector, another_sector]

salary:
  floor: 80000
  target: 95000
  cap: 120000

employer_size_priority: mid_sized
```
</details>

### Tier thresholds (Calibration writes here)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tier_thresholds.top` | float | `4.0` | Postings scoring ≥ this become TOP_TIER |
| `tier_thresholds.strong` | float | `2.5` | Postings ≥ this become STRONG |
| `tier_thresholds.exploratory` | float | `1.0` | Postings ≥ this become EXPLORATORY; below this they're SKIP |

### LLM routing (Setup Step 2 writes here)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `llm_routing.evaluation` | string | `local` | One of `local`, `anthropic`, `openai`, `gemini`, `copy_paste` |
| `llm_routing.resume` | string | `anthropic` | Same options |
| `llm_routing.form_filling` | string | `local` | Same options |

Provider keys live in `.env` (see below).

### Sources

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sources_enabled` | list[string] | `[]` | Source IDs to run on each pipeline pass |
| `jobspy.sites` | list[string] | `[indeed]` | Subset of `[indeed, google]` |
| `jobspy.cities` | list[string] | inherits `target_cities` | Per-source override |
| `jobspy.hours_old` | int | `24` | Filter to postings discovered within N hours |
| `jobspy.rate_limit_seconds` | float | `8.0` | Delay between requests |
| `linkedin_guest.keywords_traditional` | list[string] | `[]` | Search keywords |
| `linkedin_guest.locations` | list[string] | inherits `target_cities` | Per-source override |
| `greenhouse_api.slugs` / `lever_api.slugs` / etc. | list[string] | `[]` | ATS tenant slugs to query |

<details>
<summary>Example</summary>

```yaml
sources_enabled:
  - greenhouse_api
  - lever_api
  - ashby_api
  - jobspy
  - linkedin_guest
  - manual_entry

jobspy:
  sites: [indeed, google]
  cities: [your_city, another_city]
  hours_old: 24
  rate_limit_seconds: 8

linkedin_guest:
  keywords_traditional:
    - "your role keyword"
    - "another keyword"
  locations: [Your_City]

greenhouse_api:
  slugs: [acme, beta_co]
  rate_limit_seconds: 1.0
```
</details>

### Runtime knobs

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `applicant.daily_application_cap` | int | `10` | Form-filler refuses to start a new application past this limit |
| `applicant.screenshot_keep_days` | int | `90` | Form-filler screenshot retention |
| `cloud_evaluator.model` | string | `gemma-4-31b-it` | Cloud-evaluator model ID |
| `cloud_evaluator.soft_stop` | int | `1200` | Daily call cap before warning |
| `cloud_evaluator.fast_eval_reserve` | int | `100` | Reserved calls for fast-eval path |
| `cloud_evaluator.max_retries_per_posting` | int | `2` | Per-posting retry budget |

## `default_applicant.yaml` — form-filler PII

Gitignored. Setup Step 3 writes the basics; you fill the rest before your first application.

| Field | Type | Description |
|-------|------|-------------|
| `first_name` / `last_name` | string | Legal name |
| `email` | string | Contact email |
| `phone` | string | Contact phone |
| `city` | string | Current city |
| `linkedin_url` | string | Optional |
| `work_authorization` | string | Free-text status |
| `willing_to_relocate` | bool | — |
| `salary_expectation` | string | Free-text answer for open-ended salary fields |
| `salary_min` / `salary_max` / `salary_currency` | int / int / string | Numeric salary range |
| `total_years_experience` | int | — |
| `education` | list[dict] | `[{degree, school, graduation_year}, ...]` |
| `certifications` | list[dict] | `[{name, active}, ...]` |
| `demographics.gender` / `ethnicity` | string | Free-text or "Prefer not to say" |

<details>
<summary>Example</summary>

```yaml
first_name: "Your"
last_name:  "Name"
email:      "you@example.com"
phone:      "555 000 0000"
city:       "Your_City"
linkedin_url: "https://linkedin.com/in/your-handle"
work_authorization: "Permanent Resident"
willing_to_relocate: true
salary_expectation: "Competitive / Open to discussion"
salary_min: 90000
salary_max: 140000
salary_currency: "USD"
total_years_experience: 10
education:
  - degree: "MBA"
    school: "Your University"
    graduation_year: "2018"
certifications:
  - name: "PMP"
    active: true
demographics:
  gender:    "Prefer not to say"
  ethnicity: "Prefer not to say"
```
</details>

## `config/domains/<domain>.yaml`

The role-type vocabulary the discovery agent searches against.

| Field | Type | Description |
|-------|------|-------------|
| `domain` | string | Must match the filename and the profile's `domain` field |
| `role_types.<key>.description` | string | One-line summary of the role |
| `role_types.<key>.search_terms` | list[string] | Job-title strings used by ATS / aggregator queries |
| `role_types.<key>.typical_seniority` | list[string] | Subset of `[junior, mid, senior, executive]` |
| `red_flag_phrases` | list[string] | Phrases that trigger a warning badge on the card |
| `title_disambiguation` | dict | Optional employer-conditional title rules |

## `config/title_exclusions.yaml`

| Field | Type | Description |
|-------|------|-------------|
| `excluded_titles` | list[string] | Case-insensitive substring matches. Postings whose title contains any of these never reach the scorer. |

## `config/red_flag_phrases.yaml`

| Field | Type | Description |
|-------|------|-------------|
| `red_flag_phrases` | list[string] | Posting-text phrases that surface a warning on the card |

## `config/applicant_qa_patterns.yaml`

| Field | Type | Description |
|-------|------|-------------|
| `patterns` | list[dict] | Each entry: `match` (list[string], case-insensitive substrings), plus one of `answer` (literal), `answer_from_profile` (dotted attribute), or `strategy` (`upload_resume` / `upload_cover_letter` / `count_from_inventory`). First match wins. |

## `.env` — API keys

Gitignored. Setup Step 2 writes here.

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...

# Optional
JOB_AGENT_PROFILE=default
OLLAMA_HOST=http://localhost:11434
```
