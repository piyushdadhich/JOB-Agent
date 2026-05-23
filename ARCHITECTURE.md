# Architecture

A diagram-first tour of how Job Agent is wired together.

## System overview

```mermaid
graph TB
    subgraph Sources
        S1[Greenhouse / Lever / Ashby / Workday APIs]
        S2[Indeed / Google Jobs<br/>via JobSpy]
        S3[LinkedIn guest API]
        S4[Gmail inbox parser]
        S5[Manual entry]
    end

    subgraph Engine
        D[Discovery pipeline]
        X[Skill extraction]
        SC[3-signal scorer]
        EX[Expansion agent]
        AP[Form filler<br/>Playwright]
        RD[Resume drafter]
        LLM[LLM router<br/>local / cloud / clipboard]
    end

    subgraph Persistence
        DB[(SQLite<br/>tracker.db)]
    end

    subgraph UI
        DASH[FastAPI + React<br/>dashboard]
        CLI[job-agent CLI]
    end

    S1 --> D
    S2 --> D
    S3 --> D
    S4 --> D
    S5 --> D
    D --> X
    X --> SC
    SC --> DB
    DB --> DASH
    DASH --> RD
    RD --> LLM
    DASH --> AP
    AP --> LLM
    DB --> EX
    EX --> D
    CLI --> D
```

A Python monorepo, one SQLite DB per user profile, no external services required. Each layer is independently testable and the discovery sources are isolated — one failing source never blocks the pipeline.

## Data flow

```mermaid
sequenceDiagram
    participant Src as Source client
    participant Disc as Discovery pipeline
    participant DB as SQLite (tracker)
    participant Sk as Skill extractor
    participant Sc as Scorer
    participant UI as Dashboard

    Src->>Disc: discover() → Iterator[Posting]
    Disc->>DB: upsert opportunities + companies
    DB->>Sk: unscored postings
    Sk->>Sk: extract skill IDs via Lightcast / ESCO
    Sk->>Sc: posting skills + inventory skills
    Sc->>Sc: 3-signal score (coverage × rarity × bridge)
    Sc->>DB: eval_decisions row (score + tier)
    DB->>UI: shortlist (TOP_TIER + STRONG)
```

Each step is idempotent. The pipeline can resume mid-run by replaying from the last persisted state.

## Matching algorithm

```mermaid
graph LR
    P[Posting text] --> EX1[Skill extraction]
    I[Career inventory] --> EX2[Skill extraction]
    EX1 --> C[Coverage:<br/>matched / required]
    EX2 --> C
    EX1 --> R[Rarity:<br/>IDF of matched skills]
    EX2 --> R
    EX1 --> B[Bridge:<br/>nearest-match distance<br/>in Lightcast graph]
    EX2 --> B
    C --> S[Weighted sum → 0-10]
    R --> S
    B --> S
    S --> T{Tier thresholds}
    T --> TOP[TOP_TIER]
    T --> STR[STRONG]
    T --> EXP[EXPLORATORY]
    T --> SKP[SKIP]
```

**Coverage** — fraction of posting skills present in the user's inventory. A baseline measure of fit.

**Rarity** — IDF-weighted matches. A skill that appears in 5 % of postings counts more than one in 80 %, so rare-skill alignment surfaces postings most other applicants can't match.

**Bridge** — for near-miss skills, the shortest path through the Lightcast taxonomy. "Power BI ↔ Tableau" is closer than "Power BI ↔ Welding"; bridge scoring credits the former.

Final score is a weighted sum (calibratable). Tier thresholds are stored in `default.yaml` and edited by the Calibration tab via guided yes/no on sample postings.

## Database schema

```mermaid
erDiagram
    COMPANIES ||--o{ COMPANY_ALIASES : has
    COMPANIES ||--o{ OPPORTUNITIES : posts
    OPPORTUNITIES ||--o{ EVAL_DECISIONS : scored_as
    OPPORTUNITIES ||--o{ MATCH_SCORES : explains
    OPPORTUNITIES ||--o{ APPLICATIONS : pursued_as

    COMPANIES {
        int id
        string canonical_name
        string sector
        string size_band
    }
    OPPORTUNITIES {
        int id
        int company_id
        string title
        string url
        string source
        string status
        date discovered_at
    }
    EVAL_DECISIONS {
        int id
        int opportunity_id
        float score
        string tier
        string scorer_version
        date evaluated_at
    }
    MATCH_SCORES {
        int id
        int opportunity_id
        json matched_skills
        json missed_skills
        float coverage
        float rarity
        float bridge
    }
    APPLICATIONS {
        int id
        int opportunity_id
        string status
        date applied_at
        date last_touched
    }
```

Schema is versioned. Migrations under `data/schemas/v2_*/migration.sql` apply in order on `Tracker.__init__`; fresh DBs jump straight to the latest version.

## LLM routing

```mermaid
flowchart TD
    Q{Task type} --> EV[Evaluation]
    Q --> RS[Resume + cover letter]
    Q --> FF[Form-filling Q&A]

    EV --> EVR{Provider in<br/>llm_routing.evaluation}
    RS --> RSR{Provider in<br/>llm_routing.resume}
    FF --> FFR{Provider in<br/>llm_routing.form_filling}

    EVR --> L1[Ollama local]
    EVR --> A1[Anthropic]
    EVR --> O1[OpenAI]
    EVR --> G1[Gemini]
    EVR --> CP1[Copy-paste<br/>fallback]

    RSR --> L1
    RSR --> A1
    RSR --> O1
    RSR --> G1
    RSR --> CP1

    FFR --> L1
    FFR --> A1
    FFR --> O1
    FFR --> G1
    FFR --> CP1
```

Each task picks its own provider, configured per profile in `llm_routing`. A fallback chain handles transient failures: rate limits and timeouts retry-then-fall-through; auth errors fall through immediately; the chain always terminates in the unbreakable copy-paste prompt path.

## Directory layout

```
job-agent/
├── jobagent/                   CLI, scheduler, services, platform-detect
├── engine/
│   ├── discovery/              source clients (ATS, JobSpy, LinkedIn, Gmail)
│   ├── matching/               scorer, calibrator
│   ├── applicant/              form filler, worksheet, CAPTCHA, multistep
│   ├── resume/                 prompt generator, LLM router, cost estimator
│   ├── expansion/              weekly title-cluster + employer-deep + gap analyzer
│   ├── digest/                 daily-digest builder
│   ├── expiry/                 404 sweep + dismiss
│   ├── llm/                    fallback chain
│   ├── profiles/               profile + domain loader
│   └── persistence/            Tracker (SQLite) + schema migrations
├── dashboard/
│   ├── backend/                FastAPI routes
│   └── frontend/               React + Vite + Tailwind
├── config/
│   ├── profiles/               per-profile YAML (gitignored except .example)
│   └── domains/                role-type vocabularies (gitignored except .example)
├── source_materials/           per-profile inventory + resumes (gitignored)
├── data/                       per-profile SQLite + run history (gitignored)
└── tests/                      pytest suite (1,500+ tests)
```

## Adding things

| You want to add… | Read |
|------------------|------|
| A new source | ARCHITECTURE.md → Discovery section + `engine/discovery/` examples |
| A new ATS handler | `engine/applicant/handlers/` + `BaseHandler` subclass + URL pattern in `base.py` |
| A new LLM provider | `engine/resume/router.py` + `cost_estimator.py` + setup-wizard tester |
| A new dashboard tab | `dashboard/backend/routes/` + `dashboard/frontend/src/tabs/` |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the PR workflow.
