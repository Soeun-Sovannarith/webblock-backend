# WebBlock AI Code Service (AI Harness)

The **AI Code Service** (`ai_codegen`) is a high-performance FastAPI microservice responsible for converting business onboarding profiles and Puck visual page builder layouts into production-ready Next.js 16 + Drizzle ORM TypeScript codebases. It orchestrates autonomous code generation pipelines using **LangGraph** with self-healing compiler repair loops, executes structured AI code modifications with GLM (streamed, validated with Pydantic), validates builds in isolated sandboxes via the **Docker Python SDK**, and deploys live snapshot releases to GitHub.

---

## 1. Complete Harness Architecture

This section provides the complete architectural blueprint of the WebBlock AI Code Service harness. It is designed to give engineers, contributors, and operators an immediate, deep understanding of how every subsystem connects, executes, and self-heals.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                 CLIENT & INGESTION LAYER                                                        │
│                                                                                                                                 │
│   ┌─────────────────────────────────────────┐  ┌────────────────────────────────────────┐  ┌────────────────────────────────┐   │
│   │   Business Onboarding Wizard            │  │   Visual Puck Page Editor              │  │   AI Chat Assistant            │   │
│   │   • Step 1: Business Profile & Type     │  │   • Visual Drag-and-Drop Puck Blocks   │  │   • Conversational UI Updates  │   │
│   │   • Step 2: Contact Info & Schedule     │  │   • Live Visual State Editing          │  │   • Proposes Atomic Changesets │   │
│   └────────────────────┬────────────────────┘  └───────────────────┬────────────────────┘  └────────────────┬───────────────┘   │
└────────────────────────┼───────────────────────────────────────────┼────────────────────────────────────────┼───────────────────┘
                         │ POST /v1/onboardings                      │ POST …/{id}/generation-requests        │ POST /v1/conversations/{id}/messages
                         │                                           │ PATCH …/{id}/pages/{page_id}           │
                         ▼                                           ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                             FASTAPI GATEWAY & SYNTHESIS ENGINE (Port 8000)                                      │
│                                                                                                                                 │
│   ┌────────────────────────────────────────────────────────┐  ┌──────────────────────────────────────────────────────────────┐  │
│   │ Shared Catalog Schema (db/schema.ts)                   │  │ Puck Layout Synthesizer                                      │  │
│   │ (app/templates/site/db/schema.ts, scaffold.py)         │  │ (app/services/business_layout.py)                            │  │
│   │ • Same Drizzle schema for every site: `catalog`        │  │ • Generates Puck Tree (`puck-data.json`)                     │  │
│   │ • Business profile, categories, products, images       │  │ • Pre-fills Hero, Products, About, Location/Hours, Footer   │  │
│   │ • Harness-owned; no site can add tables                │  │ • Strict type checking against Puck component catalog        │  │
│   └───────────────────────────┬────────────────────────────┘  └──────────────────────────────┬───────────────────────────────┘  │
│                               │                                                              │                                  │
│                               ▼                                                              ▼                                  │
│   ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│   │ Snapshot Freezing & State Coordinator (app/services/store.py)                                                            │  │
│   │ • Hashes inputs with SHA-256 and records immutable version in `ai_codegen.input_versions`                                │  │
│   │ • Enforces single-active-run constraint (`ux_one_active_run_per_project`) via PostgreSQL advisory / unique index         │  │
│   │ • Dispatches execution job to async worker pipeline                                                                      │  │
│   └───────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────┘
                                                                │ Enqueues Run & Dispatches
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       LANGGRAPH PIPELINE STATE MACHINE (app/services/graph_pipeline.py)                         │
│                                                                                                                                 │
│       ┌─────────┐                                                                                                               │
│       │  START  │                                                                                                               │
│       └────┬────┘                                                                                                               │
│            ▼                                                                                                                    │
│   ┌──────────────────┐                                                                                                          │
│   │   prepare_node   │  • Scaffolds Next.js 16 + Drizzle site tree from built-in templates                                      │
│   │                  │  • Writes `puck-data.json`, `db/seed.sql` and the block registry                                         │
│   │                  │  • Detects structural baseline diffs and freezes `SOURCE_ARCHIVE` artifact                               │
│   └────────┬─────────┘                                                                                                          │
│            ▼                                                                                                                    │
│   ┌──────────────────┐                                                                                                          │
│   │  ai_code_update  │  • Structured AI Code Generation, streamed from GLM and validated with Pydantic                          │
│   │      (node)      │  • Validates edits against `LLMCodeResponse` Pydantic models                                             │
│   │                  │  • Enforces POSIX normalization and file security whitelist                                             │
│   └────────┬─────────┘                                                                                                          │
│            ▼                                                                                                                    │
│   ┌──────────────────┐ ◄──────────────────────────────────────────────────────────────────┐                                     │
│   │    build_node    │                                                                    │                                     │
│   │                  │  • Sandboxed Next.js 16 compilation in Docker (node:22-alpine)     │                                     │
│   │                  │  • Telemetry phases: INSTALL (cached) ➔ BUILD (isolated)          │                                     │
│   └────────┬─────────┘                                                                    │                                     │
│            │                                                                              │                                     │
│            ▼                                                                              │                                     │
│    [Build Succeeded?]                                                                     │ Autonomous Loop                     │
│    ├─── NO (Retries < 2) ──────────────────────────┐                                      │ (Self-Healing)                      │
│    │                                               ▼                                      │                                     │
│    │                                     ┌──────────────────┐                             │                                     │
│    │                                     │ compiler_repair  │ ────────────────────────────┘                                     │
│    │                                     │      (node)      │ • Feeds TypeScript/Next.js compiler stderr diagnostics to LLM     │
│    │                                     │                  │ • GLM yields targeted self-healing code edits                     │
│    │                                     └──────────────────┘                                                                   │
│    │                                                                                                                            │
│    ├─── NO (Retries >= 2) ──> [template_fallback] once, if GLM changed the code: rebuild the code from before GLM            │
│    ├─── NO (nothing left) ──> [handle_failure] ──> Mark Run FAILED in DB ──> [END]                                              │
│    │                                                                                                                            │
│    └─── YES (or SKIPPED)                                                                                                        │
│            │                                                                                                                    │
│            ▼                                                                                                                    │
│   ┌──────────────────┐                                                                                                          │
│   │   github_push    │  • Commits full generated source snapshot tree atomically to GitHub                                      │
│   │      (node)      │  • Creates / updates repository and updates `project.projects.repository_id`                             │
│   └────────┬─────────┘                                                                                                          │
│            ▼                                                                                                                    │
│   ┌──────────────────┐                                                                                                          │
│   │   deploy_node    │  • Registers deployment release in `provisioning.releases` schema                                        │
│   │                  │  • Marks run `SUCCEEDED`, writes `CHECK_REPORT`, publishes RabbitMQ `generation_completed.v1` event      │
│   └────────┬─────────┘                                                                                                          │
│            ▼                                                                                                                    │
│        ┌───────┐                                                                                                                │
│        │  END  │                                                                                                                │
│        └───────┘                                                                                                                │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                       │                                                 │
               Database Persistence    │                                                 │ Isolated Container Execution
               (Raw SQL / Zero ORM)    ▼                                                 ▼ (Docker Python SDK)
┌─────────────────────────────────────────────────────────────────┐   ┌───────────────────────────────────────────────────────────┐
│              MULTI-TENANT POSTGRESQL STATE                      │   │            DOCKER PYTHON SDK SANDBOX                      │
│                                                                 │   │                                                           │
│  • schema `project`:                                            │   │  • Base Image: `node:22-alpine`                           │
│      projects, pages, generation_requests, generation_contracts │   │  • Linux Security Capabilities: `--cap-drop ALL`          │
│  • schema `ai_codegen`:                                         │   │  • Privilege Escalation: `no-new-privileges:true`         │
│      harness_runs, input_versions, validation_runs, artifacts   │   │  • Network: `--network none` during compilation           │
│  • schema `ai_assistant`:                                       │   │  • Resource Constraints: 2.0 GB RAM, `--pids-limit 512`   │
│      conversations, messages, change_sets, change_items         │   │  • NPM Cache Volume: `webblock-npm-cache` mounted to      │
│  • schema `provisioning`:                                       │   │    `/root/.npm` for fast sub-5s incremental runs          │
│      releases, release_steps, provisioning_commands, hosting    │   │                                                           │
└─────────────────────────────────────────────────────────────────┘   └───────────────────────────────────────────────────────────┘
```

---

### Visual Architecture Flow (Mermaid)

```mermaid
flowchart TD
    %% "graph" and "end" are reserved words in Mermaid: never use them as class or node ids.
    classDef client fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0369a1;
    classDef api fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#15803d;
    classDef lg fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#b45309;
    classDef db fill:#f5f3ff,stroke:#7c3aed,stroke-width:2px,color:#6d28d9;
    classDef sandbox fill:#fdf2f8,stroke:#db2777,stroke-width:2px,color:#be185d;
    classDef external fill:#f1f5f9,stroke:#475569,stroke-width:2px,color:#334155;

    subgraph ClientLayer["1. Client & Ingestion Layer"]
        Onboarding["Business Onboarding Wizard<br/>(Step 1 Profile + Step 2 Hours)"]:::client
        Editor["Visual Puck Editor<br/>(Drag & Drop Blocks)"]:::client
        Chat["AI Design Assistant Chat<br/>(Puck change sets)"]:::client
    end

    subgraph GatewayLayer["2. FastAPI Gateway & Synthesis Engine"]
        Router["FastAPI Routers<br/>(/v1/projects, /v1/harness, /v1/conversations)"]:::api
        DrizzleGen["Onboarding Inputs<br/>(onboarding.py: catalog db/schema.ts + seed_generator → db/seed.sql)"]:::api
        PuckGen["Puck Layout Synthesizer<br/>(business_layout.py → home-page Puck tree)"]:::api
        InputFreeze["Input Version Freezer<br/>(harness.enqueue_generation → ai_codegen.input_versions)"]:::api
    end

    subgraph PersistenceLayer["3. Multi-Tenant PostgreSQL Schemas (Raw SQL)"]
        DB_Proj[("project schema<br/>(projects, pages, generation_requests)")]:::db
        DB_CodeGen[("ai_codegen schema<br/>(harness_runs, input_versions, artifacts)")]:::db
        DB_Assist[("ai_assistant schema<br/>(conversations, change_sets)")]:::db
        DB_Prov[("provisioning schema<br/>(releases, release_steps, project_hosting)")]:::db
    end

    subgraph LangGraphLayer["4. LangGraph State Machine (graph_pipeline.PIPELINE_GRAPH)"]
        G_Start(["START"]):::lg --> G_Prepare["prepare<br/>• Render templates or load baseline SOURCE_ARCHIVE<br/>• Change detection (strategy)<br/>• Write SOURCE_ARCHIVE + CHECK_REPORT"]:::lg
        G_Prepare --> G_AICode["ai_code_update<br/>• GLM, streamed, thinking off<br/>• GLM failure: keep the PREPARE code<br/>• validate_changes path guardrails<br/>• apply_changes + re-render registry"]:::lg
        G_AICode --> G_Build["build<br/>• Docker sandbox: INSTALL then BUILD<br/>• One validation_runs row per phase"]:::lg

        G_Build -->|"failed, repairs < 2, GLM configured"| G_Repair["compiler_repair<br/>• GLM fixes files from the build log<br/>• Same validate_changes guardrails"]:::lg
        G_Repair --> G_Build

        G_Build -->|"passed or skipped"| G_Push["github_push<br/>• Full-tree commit to GitHub<br/>• Sets source_commit_sha"]:::lg
        G_Build -->|"failed, no repair left, GLM changed the code"| G_Fallback["template_fallback<br/>• Restore the code from before GLM<br/>• Once per run (fallback_used)"]:::lg
        G_Fallback --> G_Build
        G_Build -->|"failed, nothing left to try"| G_Fail["handle_failure<br/>• Raises BuildError<br/>• execute_run records FAILED"]:::lg

        G_Push --> G_Deploy["deploy<br/>• PUBLISH_TARGET=github: release + deploy webhook<br/>• PUBLISH_TARGET=project: SKIPPED<br/>• Accept baseline, publish generation_completed"]:::lg
        G_Deploy --> G_Done(["END: SUCCEEDED"]):::lg
        G_Fail --> G_Failed(["END: FAILED"]):::lg
    end

    subgraph SandboxLayer["5. Isolated Execution Sandbox"]
        DockerSdk["Docker (node:22-alpine), SDK or CLI<br/>• cap-drop ALL, no-new-privileges<br/>• INSTALL: network, --ignore-scripts<br/>• BUILD: network none<br/>• Memory 2g, CPUs 2, PIDs 512, host UID"]:::sandbox
    end

    subgraph ExternalLayer["6. External Integration Services"]
        GLMEngine["LLM Engine<br/>(GLM_MODEL, default glm-4.6; OpenAI-compatible)"]:::external
        GitHubAPI["GitHub API<br/>(project repositories)"]:::external
        RabbitMQ["RabbitMQ Event Mesh<br/>(project.events in, ai_codegen.events out)"]:::external
    end

    %% Client to Gateway
    Onboarding -->|"POST /v1/onboardings"| Router
    Editor -->|"POST /v1/projects/{id}/generation-requests"| Router
    Chat -->|"POST /v1/conversations/{id}/messages"| Router
    Router --> DB_Assist

    %% Gateway to Synthesizers
    Router --> DrizzleGen
    Router --> PuckGen
    DrizzleGen --> InputFreeze
    PuckGen --> InputFreeze
    Router --> InputFreeze

    %% Freezing & DB
    InputFreeze --> DB_Proj
    InputFreeze --> DB_CodeGen

    %% Worker claims the queued run (FOR UPDATE SKIP LOCKED) and invokes the graph
    DB_CodeGen -->|"worker claims QUEUED run"| G_Start

    %% Node to External Integrations
    G_AICode <--> GLMEngine
    G_Repair <--> GLMEngine
    G_Build <--> DockerSdk
    G_Push <--> GitHubAPI
    G_Deploy --> DB_Prov
    G_Deploy --> RabbitMQ
```

---

### 1.1 Core Subsystem Breakdown

| Subsystem | Primary Implementation | Architectural Responsibility |
| :--- | :--- | :--- |
| **Ingestion & Gateway** | `app/api/routes/projects.py`<br>`app/api/routes/harness.py` | Validates incoming payloads via Pydantic; performs optimistic concurrency checks via `edit_revision`; enforces single active run per project (`ux_one_active_run_per_project`). |
| **Shared Catalog Schema** | `app/templates/site/db/schema.ts`<br>`scaffold.catalog_schema()` | The site's Drizzle schema (`pgSchema("catalog")`): the shared catalog tables of `sql/007_catalog.sql`, the same for every site. It is a harness-owned template refreshed on every run, not generated, because no site can add tables. Onboarding returns it as `drizzle_schema_preview`; a request's `drizzle_schema` no longer reaches the site. |
| **Puck Layout Synthesizer** | `app/services/business_layout.py` | Converts business metadata into a structured visual Puck layout (`puck-data.json`) including Hero, ProductCards, About, Location (with formatted operating hours and interactive map links), and Footer blocks. |
| **Seed Data Synthesizer** | `app/services/seed_generator.py`<br>`app/services/onboarding.py` | Turns the business details into `db/seed.sql`: the business profile plus the starting product catalog. The `DEPLOY` step runs it for the site's website in the shared `catalog` schema (`site_catalog.prepare_site`). In `llm` mode GLM proposes the products (`glm.request_seed_products` returns rows, never SQL); otherwise they come from the sample templates. Python escapes every value; the profile is inserted only while the website has none, and the categories and products only while it has no products, so re-seeding keeps what the owner changed in the portal. |
| **LangGraph State Machine** | `app/services/graph_pipeline.py` | Orchestrates the entire compilation, modification, self-healing, and release lifecycle as a compiled `langgraph.graph.StateGraph` with cycle detection and checkpointed state. |
| **Structured AI Code Engine** | `app/services/glm.py` | Executes code generation and repair through GLM's OpenAI-compatible API. The reply is streamed by default (`GLM_STREAM`, so a proxy such as Cloudflare doesn't cut a long generation; with `GLM_STREAM=false` it goes through **Instructor + LiteLLM** first), then validated against Pydantic models (`LLMCodeResponse`, `LLMFileChange`). |
| **Docker Build Sandbox** | `app/services/build_runner.py` | Spawns sandboxed `node:22-alpine` containers using the official **Docker Python SDK** with dropped capabilities, disabled network during builds, and cached npm dependencies. |
| **Generated Site Templates** | `app/templates/site/`<br>`app/services/scaffold.py` | Fullstack Next.js 16 site copied into every project. `app/[[...slug]]/page.tsx` renders each Puck page from `puck-data.json` and fills every `ProductCards` block from its `products` rows in the shared `catalog` schema at request time (the Puck products without database settings). Each product card links to a detail page at `/products/<slug>`. Owners manage products at `/portal`, signed in through WebBlock single sign-on (Keycloak), and changes appear on the site immediately. See [Generated site database and product portal](#generated-site-database-and-product-portal). |
| **Generated Site Catalog** | `app/services/site_catalog.py`<br>`sql/007_catalog.sql` | Every site's business profile, categories, products and product images live in the shared `catalog` schema in this service's database (a local copy of the design's Project-owned catalog), kept apart by row-level security and one login role per site (`site_<project uuid hex>`). In `DEPLOY` it creates or re-keys the site's role, registers its website, applies the seed, and returns the site's database (and object storage) settings for `site_env`. See [Generated site database and product portal](#generated-site-database-and-product-portal). |
| **Product Portal SSO** | `app/services/portal_sso.py` | Creates each site's Keycloak client (`webblock-site-<projectId>`) with an `owner` role during `DEPLOY`, and grants or revokes owners for the Project Service. See [section 9](#9-product-portal-and-single-sign-on-keycloak). |
| **Multi-Tenant State Store** | `app/services/store.py` | Manages relational persistence across 4 PostgreSQL schemas using strictly parameterized **raw SQL (`sqlalchemy.text()`)**. Completely avoids ORM overhead and migration lock-in. |

---

### 1.2 LangGraph State Machine & Self-Healing Loop

The execution pipeline is implemented using **LangGraph**. The state graph transitions deterministically through pipeline nodes while retaining an immutable execution state in `PipelineState`:

```mermaid
stateDiagram-v2
    [*] --> PREPARE: Run Enqueued

    state PREPARE {
        [*] --> ScaffoldSite: Load Baseline or Default Template
        ScaffoldSite --> InjectSynthesizedFiles: Write puck-data.json & db/seed.sql
        InjectSynthesizedFiles --> FreezeArtifacts: Write SOURCE_ARCHIVE & CHECK_REPORT
        FreezeArtifacts --> [*]
    }

    PREPARE --> AI_CODE_UPDATE: Files Scaffolded

    state AI_CODE_UPDATE {
        [*] --> CheckMode: Hybrid vs LLM Mode
        CheckMode --> CallInstructor: Prompt GLM (streamed)
        CallInstructor --> ValidatePydantic: Parse into LLMCodeResponse
        ValidatePydantic --> SanitizePaths: Normalize POSIX & Guardrails Check
        SanitizePaths --> PatchFiles: Apply LLMFileChange to in-memory tree
        CallInstructor --> KeepCode: GLM failed and optional, WARNING in run.log
        KeepCode --> [*]
        PatchFiles --> [*]
    }

    AI_CODE_UPDATE --> BUILD: Source Tree Ready

    state BUILD {
        [*] --> InstallDeps: Docker SDK -> npm install (Cached)
        InstallDeps --> NextBuild: Docker SDK -> npm run build
        NextBuild --> CaptureDiagnostics: Record stdout/stderr & timing
        CaptureDiagnostics --> [*]
    }

    state RouteDecision <<choice>>
    BUILD --> RouteDecision

    RouteDecision --> GITHUB_PUSH: Build Passed / Skipped
    RouteDecision --> COMPILER_REPAIR: Build Failed & retries < 2
    RouteDecision --> TEMPLATE_FALLBACK: Build Failed & retries >= 2 & GLM changed the code
    RouteDecision --> HANDLE_FAILURE: Build Failed & nothing left to try

    state TEMPLATE_FALLBACK {
        [*] --> RestoreCode: files = fallback_files (the PREPARE code)
        RestoreCode --> MarkUsed: fallback_used = true, WARNING in run.log
        MarkUsed --> [*]
    }

    TEMPLATE_FALLBACK --> BUILD: Rebuild Without GLM

    state COMPILER_REPAIR {
        [*] --> ExtractErrors: Parse Next.js / TypeScript Stderr
        ExtractErrors --> PromptRepairLLM: GLM self-healing diagnostic prompt
        PromptRepairLLM --> ApplyRepairPatches: Update faulty source files
        ApplyRepairPatches --> IncrementRetry: repair_attempts += 1
        IncrementRetry --> [*]
    }

    COMPILER_REPAIR --> BUILD: Re-test Repaired Code

    state GITHUB_PUSH {
        [*] --> CommitTree: Push Atomic Source Commit to GitHub
        CommitTree --> UpdateProjectRepo: Update project.projects repository metadata
        UpdateProjectRepo --> [*]
    }

    GITHUB_PUSH --> DEPLOY: Commit Published

    state DEPLOY {
        [*] --> RecordRelease: Insert provisioning.releases
        RecordRelease --> ActivateHosting: Trigger deployment worker
        ActivateHosting --> FinalizeRun: Mark SUCCEEDED in ai_codegen.harness_runs
        FinalizeRun --> PublishEvent: Emit generation_completed.v1 to RabbitMQ
        PublishEvent --> [*]
    }

    DEPLOY --> [*]: Success

    state HANDLE_FAILURE {
        [*] --> LogFailure: Write Failure Reason to Artifacts
        LogFailure --> MarkRunFailed: Update DB status to FAILED
        MarkRunFailed --> [*]
    }

    HANDLE_FAILURE --> [*]: Failed
```

#### When GLM fails: fallbacks

GLM is optional in most runs, so a GLM problem should not stop a site from shipping:

| What goes wrong | What the harness does | Run log |
|---|---|---|
| A dropped connection or HTTP 429/5xx | Retries the call once | none |
| A code file comes back garbled: on one line, where the first `//` comment would swallow the rest, or with a line break turned into a stray `n` (seen with Qwen3-Coder) | `validate_changes` rejects it and the call is made once more; a second garbled reply counts as a GLM failure (next row) | none (a warning in the service log) |
| GLM fails or times out (new site, or llm mode) | Keeps the code from `PREPARE` (templates, or the accepted site plus this run's Puck edits) and goes on | `WARNING: [AI_CODE_UPDATE] GLM failed, continuing with the code from before GLM: …` |
| GLM's code doesn't build, even after 2 `compiler_repair` attempts | `template_fallback` rebuilds the code from before GLM, once | `WARNING: [BUILD] the code GLM changed does not build; …` |
| The run applies new `instructions` to a live site | No fallback: the run fails with `GLM_FAILED` or `BUILD_FAILED`, so the request can be sent again | `FAILED [...]` |

The check report's `ai.fallback` says which fallback was used. Product suggestions at onboarding already fall back to the templates (`seed_source: "template"`).

#### The `PipelineState` TypedDict Schema

State passed between all LangGraph nodes is strictly typed:

```python
class PipelineState(TypedDict, total=False):
    run_id: str                      # Current harness run UUID
    project: dict[str, Any]          # Project metadata from project.projects
    request: dict[str, Any]          # Generation request metadata
    target: dict[str, Any]           # Target input version (frozen JSON snapshot)
    baseline: dict[str, Any] | None  # Baseline input version (if updating existing site)
    baseline_files: dict[str, str]   # Uncompressed source files of accepted baseline
    files: dict[str, str]            # In-memory virtual filesystem {path: file_content}
    summary: str                     # Human-readable progress and release summary
    report: dict[str, Any]           # Validation telemetry, model tokens, duration
    build_success: bool              # True if Docker npm run build passed
    build_error: str | None          # Compiler diagnostics / stderr if build failed
    repair_attempts: int             # Number of self-healing loop cycles completed (max 2)
    fallback_files: dict[str, str] | None  # PREPARE code, kept when GLM changed files (not for requested instructions)
    fallback_used: bool              # template_fallback ran; a second build failure ends the run
    commit_sha: str | None           # Git commit SHA produced by github_push
    error_code: str | None           # Machine-readable error code if failed
    error_message: str | None        # Detailed error log
```

---

### 1.3 End-to-End Execution Sequence Diagram

This sequence diagram illustrates the lifecycle of a request from user submission to deployed application:

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Frontend
    participant Gateway as FastAPI (/v1/projects)
    participant Synthesizer as Seed & Puck Generator
    participant DB as PostgreSQL (4 Schemas)
    participant Worker as Background Worker
    participant Graph as LangGraph Engine
    participant LLM as GLM (streamed)
    participant Docker as Docker SDK Sandbox
    participant Git as GitHub API
    participant Bus as RabbitMQ Event Bus

    User->>Gateway: POST /v1/onboardings (Onboarding details & hours)
    activate Gateway
    Gateway->>Gateway: Validate Pydantic Schema (re email, character filters)
    Gateway->>Synthesizer: seed_generator.build_seed(products, payload) -> db/seed.sql
    Gateway->>Synthesizer: generate_puck_layout(payload) -> home-page Puck tree
    Gateway->>DB: INSERT project, generation_request, input_version
    Gateway->>DB: INSERT ai_codegen.harness_runs (status='QUEUED')
    Gateway-->>User: 202 Accepted { run_id, project_id, status: 'QUEUED' }
    deactivate Gateway

    Worker->>DB: Fetch QUEUED run (with lease lock)
    Worker->>Graph: PIPELINE_GRAPH.invoke({"run_id": run_id})
    activate Graph

    Note over Graph: Node 1: prepare_node
    Graph->>DB: Load frozen input_version snapshot
    Graph->>Graph: Scaffold Next.js 16 templates (catalog db/schema.ts) + Puck blocks
    Graph->>DB: Save SOURCE_ARCHIVE artifact

    Note over Graph: Node 2: ai_code_update_node
    Graph->>LLM: Request code enhancements (Puck tree -> typed React components)
    LLM-->>Graph: Structured LLMCodeResponse (changes: [{path, content}])
    Graph->>Graph: Path security check & apply changes to virtual files

    Note over Graph: Node 3: build_node
    Graph->>Docker: docker.containers.run(node:22-alpine, npm run build)
    activate Docker
    alt Build Succeeds on First Attempt
        Docker-->>Graph: Exit Code 0 (Success)
    else Build Fails with TypeScript Errors
        Docker-->>Graph: Exit Code 1 (TypeScript compiler diagnostics stderr)
        deactivate Docker
        
        Note over Graph: Node 4: compiler_repair_node (Autonomous Self-Healing)
        Graph->>LLM: request_compiler_repair(files, compiler_stderr)
        LLM-->>Graph: LLMCodeResponse with targeted type fixes
        Graph->>Graph: Apply fixes to virtual source files
        Graph->>Docker: docker.containers.run (Re-test in Docker Sandbox)
        activate Docker
        Docker-->>Graph: Exit Code 0 (Build Passed!)
        deactivate Docker
    end

    Note over Graph: Node 5: github_push_node
    Graph->>Git: Commit atomic source tree to WebBlock-Organization repository
    Git-->>Graph: Commit SHA: ff7c17881...
    Graph->>DB: UPDATE project.projects (repository) + generation_requests.source_commit_sha

    Note over Graph: Node 6: deploy_node
    Graph->>DB: INSERT provisioning.releases & start release step
    Graph->>DB: UPDATE ai_codegen.harness_runs SET status='SUCCEEDED'
    Graph->>Bus: Publish ai_codegen.generation_completed.v1
    deactivate Graph

    User->>Gateway: GET /v1/generation-runs/{id}
    Gateway-->>User: 200 OK { status: 'SUCCEEDED', commit_sha, validations: [...] }
```

---

### 1.4 Multi-Tier Security & Sandboxing Architecture

The harness is engineered to run untrusted AI-generated code securely without exposing host resources, customer databases, or infrastructure:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    FOUR-RING DEFENSE BARRIER                                    │
│                                                                                                 │
│  RING 1: INPUT SANITIZATION & TYPE SAFETY                                                       │
│  • Pydantic v2 strict models for all endpoints (`BusinessOnboardingRequest`, etc.)              │
│  • Regex sanitization on business names, slugs, and emails (`re.compile`)                        │
│  • Stripping control characters and non-printable bytes before LLM prompt injection            │
│                                                                                                 │
│  RING 2: FILESYSTEM & PATH TRAVERSAL DEFENSE                                                    │
│  • Strict POSIX path normalization on all AI-suggested file changes                             │
│  • Immediate rejection of:                                                                      │
│      - Null bytes (`\0`)                                                                        │
│      - Windows drive paths (`C:\`, `D:\`)                                                       │
│      - Directory escapes (`..`, `../`)                                                          │
│  • Path whitelist: only `(app|components|lib|db)/**/*.(ts|tsx|css)`                             │
│  • Immutability protection: harness-owned core files (`package.json`, `theme.ts`,                │
│    `app/[[...slug]]/page.tsx`, `db/schema.ts`) CANNOT be altered by AI                          │
│                                                                                                 │
│  RING 3: DOCKER CONTAINER SANDBOXING                                                            │
│  • Clean base: official lightweight `node:22-alpine` container                                   │
│  • Complete capability stripping: `--cap-drop ALL`                                              │
│  • Privilege escalation blocked: `--security-opt no-new-privileges:true`                        │
│  • Network disabled during compilation: `--network none` during `npm run build`                 │
│  • Hard resource caps: 2.0 GB Memory limit, `--pids-limit 512`, CPU quota limits               │
│  • Non-root process execution inside the container                                              │
│                                                                                                 │
│  RING 4: DATABASE TENANCY & SQL ISOLATION                                                       │
│  • Strict multi-schema isolation (`project`, `ai_codegen`, `ai_assistant`, `provisioning`)      │
│  • Zero ORM: parameterized raw SQL (`sqlalchemy.text()`) prevents injection and bypass          │
│  • Concurrency protection: `ux_one_active_run_per_project` prevents active run race conditions  │
│  • Generated sites: shared `catalog` tables with row-level security per website; each site      │
│    logs in as its own role `site_<id>` (member of `webblock_site`): DML on its catalog rows,    │
│    no DDL, no writes to `websites`, no profile inserts                                          │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.5 Multi-Tenant Database Schema Architecture

The harness uses a four-schema database architecture in PostgreSQL (port `5433`). Foreign keys exist **strictly within the same schema**; all cross-schema references are stored as plain UUIDs to maintain complete microservice independence:

```
┌───────────────────────────────────────┐         ┌────────────────────────────────────────┐
│            SCHEMA: project            │         │          SCHEMA: ai_codegen            │
├───────────────────────────────────────┤         ├────────────────────────────────────────┤
│ projects                              │         │ harness_runs                           │
│   • project_id (UUID, PK)             │         │   • harness_run_id (UUID, PK)          │
│   • name (VARCHAR)                    │         │   • project_id (UUID)                  │
│   • subdomain (VARCHAR, UNIQUE)       │         │   • generation_request_id (UUID)       │
│   • repository_full_name (VARCHAR)    │         │   • status (QUEUED/RUNNING/SUCCEEDED)  │
│   • edit_revision (INT)               │         │   • step (PREPARE/BUILD/DEPLOY)        │
│                                       │         │   • input_tokens, output_tokens (INT)  │
│ pages                                 │         │   • commit_sha (VARCHAR)               │
│   • page_id (UUID, PK)                │         │                                        │
│   • project_id (UUID, FK -> projects) │         │ input_versions                         │
│   • puck_data (JSONB)                 │         │   • input_version_id (UUID, PK)        │
│                                       │         │   • input_hash (SHA-256)               │
│ generation_requests                   │         │   • captured_input (JSONB)             │
│   • generation_request_id (UUID, PK)  │         │                                        │
│   • project_id (UUID, FK -> projects) │         │ validation_runs                        │
│   • status (PENDING/RUNNING/SUCCEEDED)│         │   • validation_run_id (UUID, PK)       │
│   • captured_edit_revision (INT)      │         │   • harness_run_id (UUID, FK)          │
│                                       │         │   • phase (INSTALL/BUILD)              │
│ generation_contracts                  │         │   • status (PASSED/FAILED/SKIPPED)     │
│   • generation_contract_id (UUID, PK) │         │                                        │
│   • contract_version (VARCHAR)        │         │ artifacts                              │
│   • rules (JSONB)                     │         │   • artifact_id (UUID, PK)             │
└───────────────────────────────────────┘         │   • harness_run_id (UUID, FK)          │
                                                  │   • artifact_type (SOURCE/CHECK_REPORT)│
                                                  └────────────────────────────────────────┘

┌───────────────────────────────────────┐         ┌────────────────────────────────────────┐
│          SCHEMA: ai_assistant         │         │          SCHEMA: provisioning          │
├───────────────────────────────────────┤         ├────────────────────────────────────────┤
│ conversations                         │         │ releases                               │
│   • conversation_id (UUID, PK)        │         │   • release_id (UUID, PK)              │
│   • project_id (UUID)                 │         │   • project_id (UUID)                  │
│   • title (VARCHAR)                   │         │   • generation_request_id (UUID)       │
│                                       │         │   • commit_sha (VARCHAR)               │
│ messages                              │         │   • status (PENDING/SUCCEEDED)         │
│   • message_id (UUID, PK)             │         │                                        │
│   • conversation_id (UUID, FK)        │         │ release_steps                          │
│   • role (user/assistant)             │         │   • step_id (UUID, PK)                 │
│   • content (TEXT)                    │         │   • release_id (UUID, FK)              │
│                                       │         │   • step_name (DEPLOY/ACTIVATE)        │
│ change_sets                           │         │   • status (RUNNING/SUCCEEDED)         │
│   • change_set_id (UUID, PK)          │         │                                        │
│   • status (PROPOSED/APPLIED/DISCARD) │         │ project_hosting                        │
│   • page_id (UUID)                    │         │   • hosting_id (UUID, PK)              │
│                                       │         │   • project_id (UUID, UNIQUE)          │
│ change_items                          │         │   • live_release_id (UUID)             │
│   • item_id (UUID, PK)                │         │   • custom_domain (VARCHAR)            │
│   • change_set_id (UUID, FK)          │         │   • cdn_status (ACTIVE)                │
│   • operation (ADD/REMOVE/REPLACE)    │         │                                        │
└───────────────────────────────────────┘         └────────────────────────────────────────┘
```

A fifth schema, `catalog` (`sql/007_catalog.sql`), is not part of the ERD: it holds the generated sites' own data, not this service's. It is a local copy of the shared catalog that the team's database design gives to the Project Service, as `project` and `provisioning` are local copies. See [Generated site database and product portal](#generated-site-database-and-product-portal).

---

### 1.6 Source Code Directory Map

```
webblock-fastapi-harness-starter/
├── app/
│   ├── main.py                      # FastAPI application entry point, middleware, lifecycle
│   ├── api/
│   │   ├── routes/
│   │   │   ├── projects.py          # /v1/projects (pages, generation requests) & /v1/onboardings
│   │   │   ├── runs.py              # /v1/generation-runs polling, files, check report
│   │   │   ├── internal.py          # /internal/v1 inter-service mesh routes
│   │   │   └── assistant.py         # /v1/conversations & AI Assistant change sets
│   ├── core/
│   │   └── config.py                # Pydantic v2 settings (.env parsing)
│   ├── schemas/
│   │   ├── business.py              # BusinessOnboardingRequest & schedule validation
│   │   ├── codegen.py               # Generation requests and Puck contracts
│   │   └── internal.py              # Service-to-service payloads
│   └── services/
│       ├── graph_pipeline.py        # LangGraph StateGraph pipeline & self-healing nodes
│       ├── pipeline.py              # Worker run execution wrapper
│       ├── business_layout.py       # Generates puck-data.json from business profile
│       ├── seed_generator.py        # Generates db/seed.sql (profile + products) from business profile
│       ├── portal_sso.py            # Keycloak client per site for product portal single sign-on
│       ├── site_catalog.py          # Generated sites' catalog: per-site role, website row, seed, site_env
│       ├── glm.py                   # GLM client (streaming) & security parser    
│       ├── build_runner.py          # Docker Python SDK sandboxed Next.js runner
│       ├── store.py                 # Multi-schema raw SQL persistence (sqlalchemy.text)
│       ├── scaffold.py              # Base Next.js template scaffolding
│       ├── github.py                # GitHub API repository creation and atomic push
│       └── deployment.py            # Provisioning service client and release activation
├── sql/                             # the migrations, the source of truth (idempotent, numbered)
│   ├── 000_reset_legacy.sql
│   ├── 001_project.sql
│   ├── 002_ai_codegen.sql
│   ├── 003_ai_assistant.sql
│   ├── 004_provisioning.sql
│   ├── 005_style_props.sql
│   ├── 006_site_data.sql            # schema site_data (empty everywhere; dropped by 007)
│   ├── 007_catalog.sql              # schema catalog: the shared catalog for generated sites, row-level security
│   └── 008_catalog_hardening.sql    # no CREATE on public for anyone; webblock_site NOLOGIN
├── migrations/                      # Alembic: env.py (settings, DB_MIGRATION_ROLE) and one revision per sql/ file
├── alembic.ini
├── templates/
│   └── site/                        # Starter Next.js 16 + React 19 + Tailwind + Puck blocks
├── tests/                           # 91 unit and integration tests (zero-network runnable)
├── .agents/                         # AI Coding Agent custom rules and skills
├── AGENTS.md                        # Agent behavioral guidelines and repo conventions
└── docker-compose.yml               # PostgreSQL 16 on port 5433
```

---

## 2. Configuration (`.env`)

Place these environment variables in `.env`:

```ini
# Application
APP_NAME=WebBlock AI Harness
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://webblock:webblock@localhost:5433/webblock
# Production: DATABASE_URL without credentials (postgres://, postgresql:// or jdbc:postgresql:// also work), plus these.
# Add ?sslmode=require to DATABASE_URL when the server requires TLS.
USER_NAME=
DB_PASSWORD=
# Role Alembic switches to before migrating (production: ai_codegen, the database owner). Empty: the login itself.
DB_MIGRATION_ROLE=
# Generated sites' data, in this database's shared catalog schema, one login role per site (section 9.7).
# Without SITE_DB_SECRET, sites deploy with no database settings.
SITE_DB_HOST=                        # empty: DATABASE_URL's host; set it when sites reach the database by another address
SITE_DB_PORT=0                       # 0: DATABASE_URL's port
SITE_DB_NAME=                        # empty: DATABASE_URL's database
SITE_DB_SSLMODE=prefer               # libpq sslmode: disable, allow, prefer, require, verify-ca or verify-full
SITE_DB_SECRET=                      # derives each site role's password (HMAC); changing it re-keys every role on its next deploy
SITE_ROLE_ADMIN=                     # role with CREATEROLE (and ADMIN OPTION on webblock_site) to SET ROLE to when creating site roles
SITE_STORAGE_LIMIT_BYTES=104857600   # each new website's storage limit (100 MiB)
SITE_MAX_IMAGE_BYTES=5242880         # each new website's largest image or logo (5 MiB)
SITE_TIMEZONE=Asia/Phnom_Penh        # time zone of the fallback business profile and the onboarding seed
# S3-compatible object storage for product images and logos, passed to every site (path-style URLs).
# Empty: the portal refuses uploads, and products can be added without a photo.
SITE_STORAGE_ENDPOINT=
SITE_STORAGE_REGION=us-east-1
SITE_STORAGE_BUCKET=
SITE_STORAGE_ACCESS_KEY_ID=
SITE_STORAGE_SECRET_ACCESS_KEY=
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
REQUEST_TIMEOUT_SECONDS=90

# GLM LLM Engine (OpenAI-compatible)
GLM_BASE_URL=https://llm.example.com/v1
GLM_API_KEY=your_glm_api_key
GLM_MODEL=glm-4.7-flash
GLM_TIMEOUT_SECONDS=180          # cap on one GLM call
GLM_STREAM=true                  # stream replies; behind Cloudflare a silent call is cut with HTTP 524 after ~100 s
GLM_THINKING=false               # true lets GLM reason before answering: often 3x slower, the build catches its mistakes
CF_ACCESS_CLIENT_ID=your_cloudflare_access_client_id
CF_ACCESS_CLIENT_SECRET=your_cloudflare_access_client_secret

# Codegen Strategy Mode
# "hybrid" = Deterministic scaffold + GLM only on explicit prompt instructions
# "llm"    = Pure LLM flow: GLM turns Puck JSON directly into React components & updates
CODEGEN_MODE=llm

# GitHub Integration
GITHUB_ORGANIZATION_NAME=WebBlock-Organization
GITHUB_TOKEN=ghp_your_github_token
GIT_BRANCH=main

# Who publishes a successful run (section 10)
PUBLISH_TARGET=github                # github: this service pushes and deploys
                                     # project: the platform does (Project -> Gitea, Provisioning builds and deploys)
ARTIFACT_URL_SECRET=change_me        # signs the artifact download URLs Project gets; empty = runs carry no URL
ARTIFACT_BASE_URL=http://ai-codegen:8000   # how the Project Service reaches this service
ARTIFACT_URL_TTL_SECONDS=600         # 30-600: how long a download URL works

# Worker & Build Runner
RUN_WORKER=true
BUILD_RUNNER=docker
BUILD_IMAGE=node:22-alpine
STRICT_PROMOTION=false

# RabbitMQ Event Mesh
RUN_CONSUMER=false
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USERNAME=webblock
RABBITMQ_PASSWORD=webblock

# Downstream Microservices
PROJECT_SERVICE_URL=http://localhost:8082
PROVISIONING_SERVICE_URL=http://localhost:8084
KEYCLOAK_ISSUER_URI=http://localhost:8180/realms/webblock
KEYCLOAK_JWK_SET_URI=http://localhost:8180/realms/webblock/protocol/openid-connect/certs

# Product portal single sign-on (section 9). Empty admin client = sites deploy with the portal disabled.
KEYCLOAK_ADMIN_CLIENT_ID=webblock-harness
KEYCLOAK_ADMIN_CLIENT_SECRET=your_admin_client_secret
# Admin REST API host, when the realm's public host doesn't serve it (no /realms). Empty: the issuer's host.
KEYCLOAK_ADMIN_URL=
# Cloudflare Access token sent to KEYCLOAK_ADMIN_URL only; unset, it is CF_ACCESS_CLIENT_ID/SECRET (shared with GLM).
# KEYCLOAK_CF_ACCESS_CLIENT_ID=
# KEYCLOAK_CF_ACCESS_CLIENT_SECRET=
KEYCLOAK_SITE_CLIENT_PREFIX=webblock-site-
SITE_URL_TEMPLATE=https://{subdomain}.sites.example.com
```

---

## 3. Internal Microservice APIs (`/internal/v1`)

These endpoints are designed for inter-service communication (e.g., called by **Project Service** and **Provisioning Service**). They do not require browser cookies or frontend sessions.

Errors no route handled answer as Problem Details here: `503` with `code` `SERVICE_UNAVAILABLE` and `Retry-After: 5` when the database is unreachable, `500` with `code` `INTERNAL_ERROR` otherwise (`app/api/errors.py`).

### Product portal owners: `GET|PUT|DELETE /internal/v1/projects/{project_id}/portal-owners[/{user_id}]`

These are the only write endpoints under `/internal/v1`. The Project Service calls them when project membership changes, so that exactly the project's members can use the site's product portal.

- `GET` returns `[{"user_id": "...", "username": "...", "email": "..."}]`.
- `PUT` grants and `DELETE` revokes; both answer `204` and are idempotent.
- `user_id` is the Keycloak user ID.
- Errors: `404` (the site has no Keycloak client yet; it is created on the first deploy), `502` (Keycloak error), `503` (portal SSO not configured).
- Full explanation: [section 9.6](#96-who-can-manage-a-site-owners).

### `GET /internal/v1/health`
Health check for service mesh and load balancers.
- **Request**:
  ```bash
  curl -s http://localhost:8000/internal/v1/health
  ```
- **Response `200 OK`**:
  ```json
  {
    "status": "ok",
    "service": "ai_codegen",
    "mesh_protocol": "internal/v1"
  }
  ```

---

### Endpoints the Project Service reads (its contract, section 2.4)

These three follow the platform's HTTP conventions (Project's `service-communication.md`):
- A successful body is the envelope `{"success": true, "data": ..., "meta": {...}}`, with camelCase fields.
- An error is RFC 9457 Problem Details: `type` `urn:webblock:problem:<code>`, `title`, `status`, `instance`, `code`.
- `meta.requestId` echoes the caller's `X-Request-Id`.
- There is no token: in the cluster, the Istio mesh policy decides who may call `/internal/**`.

#### `GET /internal/v1/generation-runs/{run_id}`
The run Project reads after `ai_codegen.generation_completed.v1`. It is the authority for the outcome.
- **Request**:
  ```bash
  curl -s http://localhost:8000/internal/v1/generation-runs/376bf1a3-4526-4af3-9da0-c8d66bf8493a
  ```
- **Response `200 OK`**:
  ```json
  {
    "success": true,
    "data": {
      "generationRunId": "376bf1a3-4526-4af3-9da0-c8d66bf8493a",
      "generationRequestId": "ff2f99e3-70e8-4a32-ac64-641ff058a36a",
      "projectId": "5a8c79b3-e7bf-48d5-98ae-c34e20c2c79f",
      "inputHash": "cfd1588de80fda3a9402470e6eabce6fd241f47b933ae630aa4adce0c0abef77",
      "status": "succeeded",
      "attempt": 1,
      "errorCode": null,
      "artifacts": [
        { "artifactType": "check_report", "contentHash": "<sha256 hex>", "byteSize": 1362,
          "downloadUrl": "http://ai-codegen:8000/internal/v1/artifacts/<artifactId>?expires=1793350000&signature=<hmac>" },
        { "artifactType": "run_log", "contentHash": "<sha256 hex>", "byteSize": 1764, "downloadUrl": "..." },
        { "artifactType": "source_archive", "contentHash": "<sha256 hex>", "byteSize": 38026, "downloadUrl": "..." }
      ]
    },
    "meta": { "requestId": "…", "traceId": null, "timestamp": "2026-10-30T00:12:03.120Z",
              "path": "/internal/v1/generation-runs/376bf1a3-…", "status": 200, "page": null }
  }
  ```
- **The fields:**
  - `status` is `queued`, `running`, `succeeded` or `failed`.
  - `inputHash` is Project's `capturedInputHash` for the request (the JCS SHA-256 of the input).
  - `errorCode` is this service's code: `BUILD_FAILED`, `GLM_FAILED`, `INPUT_HASH_MISMATCH`, `CANCELLED`, and so on.
- **The artifacts:**
  - `source_archive` is a gzip tar of the **complete** generated site. Project's contract expects an overlay limited to its scaffold's `allowedPaths`; until Project serves that scaffold (its J4), we send the full site (section 10).
  - `check_report` is the JSON in `/check-report` below.
  - `run_log` is the run's log.
  - `contentHash` is the SHA-256 of the bytes behind `downloadUrl`.
- **`downloadUrl`** is signed with `ARTIFACT_URL_SECRET` and works for `ARTIFACT_URL_TTL_SECONDS` (at most 10 minutes). It is `null` when no secret is set.
- **Error `404`** (`application/problem+json`):
  ```json
  { "type": "urn:webblock:problem:generation-run-not-found", "title": "No generation run with this ID was found.",
    "status": 404, "instance": "/internal/v1/generation-runs/…", "code": "GENERATION_RUN_NOT_FOUND",
    "requestId": "…", "traceId": null }
  ```

#### `GET /internal/v1/generation-requests/{request_id}/runs`
Every run of a request, newest first, as a list of the documents above in `data`. Project uses it for reconciliation when its deadline passes. Query: `limit` (default `20`, max `100`). An unknown request returns `"data": []`.

#### `GET /internal/v1/artifacts/{artifact_id}?expires=…&signature=…`
The address behind a `downloadUrl`. It returns the stored bytes:
- `application/gzip` for the source archive;
- `application/json` for the check report;
- `text/plain` for the log.

A changed, expired or unsigned URL answers `403` with `code` `DOWNLOAD_URL_INVALID`. An unknown artifact answers `404` with `ARTIFACT_NOT_FOUND`.

---

### `GET /internal/v1/generation-runs/{run_id}/files`
Retrieves the uncompressed file tree snapshot generated by this run. Useful for Provisioning Service or Project Service to inspect source code without pulling from Git.
- **Path Parameters**: `run_id` (UUID)
- **Request**:
  ```bash
  curl -s http://localhost:8000/internal/v1/generation-runs/ee7067f6-4d57-49e6-bebe-b69730639605/files
  ```
- **Response `200 OK`**:
  ```json
  {
    "package.json": "{\n  \"name\": \"webblock-site\",\n  \"scripts\": { \"build\": \"next build\" } ...\n}",
    "app/layout.tsx": "export default function RootLayout({ children }) { ... }",
    "components/blocks/Hero.tsx": "export function Hero(props) { ... }"
  }
  ```

---

### `GET /internal/v1/generation-runs/{run_id}/check-report`
Downloads the JSON validation report containing compilation duration, errors (if any), and AI usage metadata.
- **Path Parameters**: `run_id` (UUID)
- **Request**:
  ```bash
  curl -s http://localhost:8000/internal/v1/generation-runs/ee7067f6-4d57-49e6-bebe-b69730639605/check-report
  ```
- **Response `200 OK`**:
  ```json
  {
    "run_id": "ee7067f6-4d57-49e6-bebe-b69730639605",
    "strategy": "SOURCE_CODE",
    "summary": "Generated Next.js + Drizzle project (22 files).",
    "validations": [
      { "type": "INSTALL", "status": "PASSED", "duration_ms": 119645 },
      { "type": "BUILD", "status": "PASSED", "duration_ms": 5224 }
    ],
    "ai": {
      "model_name": "glm-4.7-flash",
      "input_tokens": 4689,
      "output_tokens": 1375,
      "files": ["components/blocks/Hero.tsx"]
    }
  }
  ```
  When a fallback was used, `ai` also has `fallback`, for example `"GLM failed: GLM did not finish within 180 s."` (the site was built from the code before GLM) or `"GLM's code did not build; built without it."`. See section 1.2.

---

## 4. RabbitMQ Event Contracts (with the Project Service)

The format is the platform's (Project's `service-communication.md`, "Message conventions"; the same envelope as Provisioning's `events/envelope.v1.schema.json`).

- **Exchanges.** Each producing service has one durable topic exchange. We publish to **`ai_codegen.events`** and consume from **`project.events`**. Project declares `project.events` (with an alternate exchange), so we declare it passively: the consumer keeps retrying until Project has declared it.
- **Routing key** is `<event>.v<N>`, for example `generation_completed.v1`.
- **Bodies** are snake_case and carry identifiers, not content. Every body has the envelope fields:
  - `schema_version` (1);
  - `message_id` (a UUIDv7 in what we publish);
  - `message_type` (`<service>.<event>.v<N>`);
  - `occurred_at` (RFC 3339);
  - `correlation_id` (the generation request ID);
  - `project_id`.
- **AMQP properties:** `type` = `message_type`, `message_id` = the body's, `application/json`, UTF-8, persistent. Publishing uses publisher confirms.
- **Queues** (we own them):
  - `ai_codegen.generation_requested.v1` and `ai_codegen.generation_cancelled.v1`.
  - Both are quorum queues with a delivery limit of 10, each with a dead-letter queue `<queue>.dlq`.
  - A malformed message goes straight to the dead-letter queue.
  - A message we can't handle yet (Project unreachable, or another run of the project active) is redelivered after 5 s. If it hits the limit and is parked, nothing is lost: Project's generation deadline publishes the request again.
- The consumer runs with `RUN_CONSUMER=true`.

### Inbound: `project.generation_requested.v1`
Payment admitted the request and it is generating.
```json
{
  "schema_version": 1,
  "message_id": "0192f3a4-5b6c-7d8e-9f01-23456789abcd",
  "message_type": "project.generation_requested.v1",
  "occurred_at": "2026-10-30T00:10:00.123456Z",
  "correlation_id": "ff2f99e3-70e8-4a32-ac64-641ff058a36a",
  "project_id": "5a8c79b3-e7bf-48d5-98ae-c34e20c2c79f",
  "generation_request_id": "ff2f99e3-70e8-4a32-ac64-641ff058a36a",
  "captured_input_hash": "cfd1588de80fda3a9402470e6eabce6fd241f47b933ae630aa4adce0c0abef77"
}
```
What we do (`harness.handle_generation_requested_event`):
1. **One run per request.**
   - If the request already has a finished run, we publish its completion again.
   - If its run is still queued or running, we do nothing.
2. **Read the input.** We call `GET {PROJECT_SERVICE_URL}/internal/v1/generation-requests/{id}/codegen-input` (section 5).
   - `409 REQUEST_NOT_GENERATING` or `404`: we drop the message.
   - Unreachable or `5xx`: we retry.
3. **Check the input.** It must belong to this request and project, and its JCS SHA-256 (`app/services/jcs.py`) must equal both `captured_input_hash` and the document's `capturedInputHash`. Otherwise the request ends with a failed run, `INPUT_HASH_MISMATCH`, and a completion event.
4. **Queue the run.** We save the input as an `ai_codegen.input_versions` row and queue a run whose `input_hash` is that hash. Project's pages become the site's pages. Its input has no AI instructions, Drizzle schema or seed, and the mode is `CODEGEN_MODE`.

### Inbound: `project.generation_cancelled.v1`
The envelope plus `generation_request_id`. Project sends it when the request passes its hard deadline.

We mark the request's active run `FAILED` with `error_code` `CANCELLED`. This publishes no completion, because Project ignores completions for requests that are no longer generating.

### Outbound: `ai_codegen.generation_completed.v1`
Published once when a request's run ends, whether it succeeded or failed (and again if the request event repeats). The routing key is `generation_completed.v1` on `ai_codegen.events`.
```json
{
  "schema_version": 1,
  "message_id": "019a32b8-0a41-7c3e-8f5d-6a7b8c9d0e1f",
  "message_type": "ai_codegen.generation_completed.v1",
  "occurred_at": "2026-10-30T00:12:00.415000Z",
  "correlation_id": "ff2f99e3-70e8-4a32-ac64-641ff058a36a",
  "project_id": "5a8c79b3-e7bf-48d5-98ae-c34e20c2c79f",
  "generation_request_id": "ff2f99e3-70e8-4a32-ac64-641ff058a36a",
  "generation_run_id": "376bf1a3-4526-4af3-9da0-c8d66bf8493a",
  "outcome": "succeeded"
}
```
`outcome` is `succeeded` or `failed`. Everything else, such as the error code and the artifacts, is in the run (`GET /internal/v1/generation-runs/{id}`, section 3).

Publishing is best-effort: with the broker down, the run still ends and a warning is logged. This service has no outbox yet. Project recovers through its deadline, by reading the request's runs.

---

## 5. Outbound HTTP Calls to Other Services

The AI Code Service makes outbound HTTP requests to **Project Service** (`PROJECT_SERVICE_URL`):

1. **`GET /internal/v1/generation-requests/{generationRequestId}/codegen-input`**, on `project.generation_requested.v1` (`project_client.fetch_codegen_input`, never raises):
   - **Answer:** the frozen input in Project's envelope (`{success, data, meta}`). `data` has `generationRequestId`, `projectId`, `businessTypeCode`, `capturedInput` (`componentCatalogVersion`, `generationContractId`, `pages[]` with `pageId`, `path`, `name`, `sortOrder`, `seoTitle`, `seoDescription` and `puckTree`), `capturedInputHash` and `generationContract`.
   - **Outcomes:** `409` (`REQUEST_NOT_GENERATING`) means stop. `404` means unknown. `503` or unreachable means retry later.
2. **`POST /internal/v1/projects/{projectId}/access-checks`**:
   - Validates user permissions before allowing chat modifications on a project:
     ```json
     { "user_id": "...", "action": "manage_site" }
     ```

It also calls:

3. **Keycloak Admin REST API** (`KEYCLOAK_ISSUER_URI`'s server, as the `KEYCLOAK_ADMIN_CLIENT_ID` service account), only when portal SSO is configured:
   - In the `DEPLOY` step, to create or update the site's client `webblock-site-<projectId>`, its `owner` role and claim mapper, and to give the project owner the role.
   - From the `/internal/v1/projects/{id}/portal-owners` endpoints.
   - See [section 9](#9-product-portal-and-single-sign-on-keycloak).
4. **The deploy webhook** (`DEPLOYMENT_WEBHOOK_URL`, `DEPLOYMENT_PROVIDER=webhook`) in the `DEPLOY` step. With the site catalog (`SITE_DB_SECRET`) or portal SSO configured, its body includes `site_env`: the site's database settings (its own role and password), its object storage settings when `SITE_STORAGE_*` is set, and its SSO variables (contract in [section 9.8](#98-deploy-webhook-contract-site_env)).
5. **GLM** (`POST {GLM_BASE_URL}/chat/completions`, OpenAI-compatible), with `Authorization: Bearer GLM_API_KEY` and, for a server behind Cloudflare Access, the `CF-Access-Client-Id`/`CF-Access-Client-Secret` headers:
   - In `AI_CODE_UPDATE` and `compiler_repair` (code), during onboarding (products) and from the design assistant (Puck operations).
   - JSON mode, streamed as server-sent events, with thinking turned off (`chat_template_kwargs.enable_thinking=false` and `thinking.type=disabled`; a server ignores the one it doesn't know). See [section 7](#7-codegen-modes-llm-vs-hybrid).

---

## 6. Public REST APIs (`/v1`) — Used by Editor & Frontend

These endpoints support the standalone UI / Editor at `http://localhost:3000`. The interactive version, with every request and response schema, is at `http://localhost:8000/docs`.

**REST conventions.** Paths name resources (plural nouns, nested under their project), and the HTTP method is the action: `GET` reads, `POST` creates, `PATCH` changes part of a resource, `DELETE` removes it. Status codes (errors are `{"detail": …}` here, Problem Details under `/internal/v1`):

| Code | When |
|---|---|
| `200 OK` | A read, a `PATCH`, a `DELETE` (whose body carries the new `edit_revision`), or a generation request with nothing left to do: no change since the last successful run (`run_id: null`), or an `Idempotency-Key` replay of a run that already finished |
| `201 Created` | A project, page, conversation or message was created; `Location` is the new resource's URL |
| `202 Accepted` | A generation run is queued or running; `Location: /v1/generation-runs/{run_id}` is the run to poll |
| `404 Not Found` | The project, page, run, conversation or change set does not exist, or the path is unknown |
| `405 Method Not Allowed` | The path exists but not with this method |
| `409 Conflict` | Another run is active for the project, `base_edit_revision` is stale, the page path is taken, the home page (`/`) is being deleted, the subdomain is taken, or the change set was already decided |
| `422 Unprocessable Content` | The body, a path id or a query parameter failed validation (`detail` lists the fields), an invalid Puck tree, a reserved page path (`/portal`, `/healthz`, `/readyz`), or an `Idempotency-Key` reused with a different body |
| `500 Internal Server Error` | An unexpected error; the body is `{"detail": "An unexpected error occurred."}` and the server log has the traceback |
| `502 Bad Gateway` | GLM (the design assistant) or Keycloak failed or answered with something unusable |
| `503 Service Unavailable` | The database is unreachable (with `Retry-After: 5`), or GLM / portal single sign-on is not configured |

Error bodies never contain the exception text, which could carry connection strings or SQL.


### Integration guide: business info → generated code → GitHub

Four calls cover the whole flow. Everything after step 1 happens in the background worker, so the client polls.

**1. Send the business info.** This creates the project, returns the site's database schema and seed straight away, and queues the generation (body: [section 6C](#c-projects-and-pages-crud)):
```bash
curl -si -X POST http://localhost:8000/v1/onboardings \
  -H 'content-type: application/json' -d @business.json
```
```json
{ "project_id": "1d0fb151-…", "project_name": "Sok Coffee", "generation_request_id": "…", "run_id": "8d90a030-…",
  "status": "QUEUED", "drizzle_schema_preview": "// Owned by the WebBlock harness … pgSchema(\"catalog\") …",
  "seed_sql_preview": "-- Seed data … INSERT INTO catalog.business_profiles …",
  "seed_source": "glm", "puck_blocks": [ … ], "message": "…" }
```
The `Location` header is `/v1/generation-runs/8d90a030-…`. Show `drizzle_schema_preview` (the site's `db/schema.ts`) and `seed_sql_preview` (its `db/seed.sql`) to the user if you like; the generated site will contain exactly these.

**2. Poll the run** every 3–5 s until `status` is `SUCCEEDED` or `FAILED`:
```bash
curl -s http://localhost:8000/v1/generation-runs/8d90a030-…
```
| Field | Meaning |
|---|---|
| `status` | `QUEUED`, `RUNNING`, `SUCCEEDED` or `FAILED` |
| `step` | Current step: `PREPARE`, `AI_CODE_UPDATE`, `BUILD`, `GITHUB_PUSH`, `DEPLOY`, then `DONE` |
| `log` | The run log. `SKIPPED:` lines are steps that were not configured; `WARNING:` lines are fallbacks ([section 1.2](#12-langgraph-state-machine--self-healing-loop)) |
| `repository_url`, `commit_sha` | **The GitHub repo and the commit with the generated code** (null when GitHub is not configured) |
| `changed_files`, `validations` | What changed, and the `INSTALL`/`BUILD` results |
| `error_code`, `errors` | Why a run failed, for example `GLM_FAILED` or `BUILD_FAILED` |

**3. Get the code.** It is in GitHub at `repository_url` (branch `GIT_BRANCH`, default `main`), one private repo per project under `GITHUB_OWNER`. Without GitHub, or to show it in the editor:
```bash
curl -s http://localhost:8000/v1/generation-runs/8d90a030-…/files          # {"path": "file content", …}
curl -s http://localhost:8000/v1/generation-runs/8d90a030-…/check-report   # summary, changed files, AI usage, fallbacks
```

**4. Later changes** push a new commit to the same repo. Send the edited home page and, optionally, a request for GLM:
```bash
curl -si -X POST http://localhost:8000/v1/projects/1d0fb151-…/generation-requests -H 'content-type: application/json' \
  -H 'Idempotency-Key: edit-42' -d '{"puck_json": { … }, "instructions": "Add a newsletter sign-up"}'
```
It answers with a new `run_id` (and `Location`); poll it the same way. To change other pages, save them with `PATCH /v1/projects/{id}/pages/{page_id}` first, then send the request without `puck_json`.

**Things to handle:**
- `409`: the project already has a run in progress (one at a time). Wait for it, then send again.
- `422`: the body failed validation; `detail` says which field.
- `run_id: null`: nothing changed since the last successful run, so nothing was generated.
- Other services can read `/internal/v1/generation-runs/{id}` (Project's run document, [section 3](#3-internal-microservice-apis-internalv1)) or listen for `ai_codegen.generation_completed.v1` ([section 4](#4-rabbitmq-event-contracts-with-the-project-service)) instead of polling.

Tested end to end through this API on 29 Oct 2026: business info → GLM products and code → Docker build → private repo `WebBlock-Organization/sok-coffee-1d0fb151` (initial commit), then an update through step 4 pushed a second commit to the same repo.

### A. Generation requests & runs

#### `POST /v1/projects/{project_id}/generation-requests`
Generates the site (first time) or updates it from the project's current pages. With `puck_json`, the home page's Puck tree is replaced first (the editor's save-and-generate).
- **Request Headers**:
  - `Content-Type: application/json`
  - `Idempotency-Key: <any string up to 255 chars>` *(optional; repeating it returns the original request's run, reusing it with a different body is a `422`)*
  - `X-Codegen-Mode: llm` *(optional, `hybrid` or `llm`; overrides `CODEGEN_MODE` from `.env`; the body's `codegen_mode` wins over both)*
- **Request Body** (every field optional):
  ```json
  {
    "puck_json": {
      "root": { "props": { "title": "Home", "theme": "warm" } },
      "content": [
        { "type": "Hero", "props": { "id": "hero-1", "title": "Welcome to WebBlock", "subtitle": "Generated with AI" } }
      ]
    },
    "instructions": "Change the hero button to emerald green",
    "drizzle_schema": null,
    "codegen_mode": "llm"
  }
  ```
  `drizzle_schema` is still accepted and recorded in the captured input, but it no longer reaches the site: every site's `db/schema.ts` is the shared catalog template.
- **Response `202 Accepted`**, with `Location: /v1/generation-runs/ee7067f6-…`:
  ```json
  {
    "run_id": "ee7067f6-4d57-49e6-bebe-b69730639605",
    "generation_request_id": "…",
    "status": "QUEUED",
    "project_id": "8093633a-08eb-41f0-9683-de8b300be240",
    "flow": "INITIAL_GENERATION",
    "strategy": "SOURCE_CODE"
  }
  ```
  When nothing changed since the last successful run, `run_id` is `null`, `status` is `SUCCEEDED` and there is no `Location`.

#### `GET /v1/projects/{project_id}/generation-requests?limit=20`
The project's generation requests, newest first.

#### `GET /v1/generation-runs/{run_id}`
Polls the live progress of a run: `status`, `step`, `log`, `repository_url`, `commit_sha`, `changed_files`, `validations`, `error_code`, `errors` (see the integration guide above). `404` if the run does not exist.

#### `GET /v1/generation-runs/{run_id}/files` and `GET /v1/generation-runs/{run_id}/check-report`
The run's full source snapshot (`{"path": "content"}`) and its check report.

#### `GET /v1/projects/{project_id}/generation-runs?limit=20`
The project's runs, newest first.

---

### B. AI Design Assistant

#### `POST /v1/projects/{project_id}/conversations`
Creates an AI chat session.
- **Request Body**: `{"title": "Redesign discussion"}`
- **Response `201 Created`**, with `Location: /v1/conversations/{conversation_id}`:
  ```json
  {
    "conversation_id": "91bd2ad9-c1d4-4167-8550-ef9640e89a5b",
    "project_id": "8093633a-08eb-41f0-9683-de8b300be240",
    "title": "Redesign discussion"
  }
  ```

#### `POST /v1/conversations/{conversation_id}/messages`
Sends a prompt to the AI Assistant. The assistant evaluates available component definitions and proposes visual Puck operations.
- **Request Body**:
  ```json
  {
    "content": "Add a Hero section with a CTA button",
    "page_id": "optional-page-uuid"
  }
  ```
- **Response `201 Created`**:
  ```json
  {
    "user_message": {
      "message_id": "840c8d7b-db45-4c8e-a687-b3794b9b7ee6",
      "content": "Add a Hero section with a CTA button"
    },
    "assistant_message": {
      "message_id": "9296fc06-447f-4122-8b05-ffdbe37db8f3",
      "content": "I have added a modern Hero component with a call to action button.",
      "model_name": "glm-4.7-flash",
      "input_tokens": 876,
      "output_tokens": 537
    },
    "change_set": {
      "change_set_id": "67b936d8-65cb-42bb-9273-0498eb9bf0e2",
      "status": "PROPOSED",
      "items": [
        { "operation": "ADD", "component_type": "Hero", "component_id": "hero-cta" }
      ]
    }
  }
  ```

#### `GET /v1/conversations/{conversation_id}`
The conversation with its `messages` and `change_sets`.

#### `GET /v1/change-sets/{change_set_id}`
One change set with its `items`.

#### `PATCH /v1/change-sets/{change_set_id}`
Decides a `PROPOSED` change set.
- **Request Body**: `{"status": "APPLIED"}` or `{"status": "DISCARDED"}`
- **Response `200 OK`**: `APPLIED` rewrites the page and answers `{"change_set": …, "page": …, "edit_revision": 7}`; `DISCARDED` answers `{"change_set": …}`.
- **`409`** if the change set was already decided, or if another edit landed after it was proposed (it becomes `REJECTED` with `STALE_EDIT_REVISION`).

---

### C. Projects, pages and onboarding

- `POST /v1/onboardings`: **Onboarding One-Step Generator**. Creates a project from the 2-step onboarding payload (details + schedule), returns the shared catalog's Drizzle schema (`db/schema.ts`, the same for every site), synthesizes the seed script for the site's catalog rows and pre-filled Puck page blocks, and starts the LangGraph generation pipeline.
- `POST /v1/projects/{id}/onboardings`: Applies business onboarding data to an existing project and starts generation.

Request body (`BusinessOnboardingRequest`; only `business_name` and `business_description` are required):
```json
{
  "business_name": "Sok Coffee",
  "business_type": "food",
  "business_description": "A small specialty coffee shop in Phnom Penh serving Cambodian-grown beans and fresh pastries.",
  "logo_url": null,
  "phone_number": "012 345 678",
  "email": "hello@sokcoffee.example",
  "address": "St 240, Phnom Penh",
  "opening_hours": [{ "day": "Monday", "is_open": true, "opens": "7:00 AM", "closes": "6:00 PM" }],
  "theme": "warm",
  "accent": "#a4532b",
  "font": "sans",
  "codegen_mode": "llm",
  "instructions": null
}
```
`theme` is `warm`, `light` or `dark`; `font` is `sans`, `serif` or `mono`; `accent` is `#rrggbb`. Leave out `codegen_mode` to use the service's `CODEGEN_MODE`.

Both onboarding endpoints answer `202` with `project_id`, `generation_request_id`, `run_id`, `status`, `drizzle_schema_preview` (the site's `db/schema.ts`), `seed_sql_preview` (the site's `db/seed.sql`), `seed_source` (`"glm"` when GLM proposed the products, which happens only in `llm` mode (the request's `codegen_mode`, else `CODEGEN_MODE`) with GLM configured, otherwise `"template"`, including after a GLM failure), `puck_blocks` and `message` (`POST /v1/onboardings` adds `project_name`), and `Location: /v1/generation-runs/{run_id}` when a run was queued. The home page's `ProductCards` block lists the same products as the seed.

- `POST /v1/projects`: `{"name": "My Site", "subdomain": "my-site", "repository_url": null}` → `201`, creates the project and its home page (`/`), `Location: /v1/projects/{id}`.
- `GET /v1/projects/{id}`: Returns project details and current `edit_revision`.
- `GET /v1/projects/{id}/versions/latest`: Returns home page Puck JSON tree for the editor.
- `GET /v1/projects/{id}/pages`: Lists all project pages.
- `POST /v1/projects/{id}/pages`: `{"name": "About", "path": "/about", "puck_tree": {…}, "seo_title": …, "seo_description": …, "sort_order": 1}` → `201` with the page and the new `edit_revision`, `Location: /v1/projects/{id}/pages/{page_id}`. `409` for a duplicate path, `422` for a reserved one (`/portal`, `/healthz`, `/readyz`).
- `GET /v1/projects/{id}/pages/{page_id}`: One page.
- `PATCH /v1/projects/{id}/pages/{page_id}`: Saves page Puck tree. Supports optimistic locking via `base_edit_revision` (`409 Conflict` on stale edit).
- `DELETE /v1/projects/{id}/pages/{page_id}`: `200` with `{"deleted": true, "edit_revision": 8}`; the body carries the new revision the editor needs for its next `base_edit_revision`.
- `GET /v1/projects/{id}/hosting` and `GET /v1/projects/{id}/releases?limit=20`: Provisioning's view of the site (read-only).

#### Generated site database and product portal

Every generated site is a fullstack app. Its data lives in this service's database, in the shared schema `catalog` (`sql/007_catalog.sql`), not in a database of its own. The schema follows the team's database design ("Database Design", section "Shared catalog"); `007` also drops the `site_data` schema of `006`, which held no rows anywhere.

- **Tables, as designed**: the reference lists `business_types`, `product_types` and `product_type_schemas` (versioned `field_schema`), then `websites` (one per project, `website_id` = the project id; `db_role_name`, status `active`/`suspended`/`archived`, `storage_limit_bytes`, `max_image_bytes`, `storage_used_bytes`), `business_profiles` (primary key `website_id`), `categories`, `products` (UUID ids, `price NUMERIC(12,2)` in USD, where NULL hides the price, `attributes`, status `draft`/`published`/`archived`, `row_version`) and `product_images` (`storage_key`, `byte_size`).
- **One login role per site**: `site_<project uuid hex>` (`site_catalog.role_name`), a member of the `NOLOGIN` group `webblock_site`, which holds the grants. The role's password (`site_catalog.role_password`) is an HMAC of the project id under `SITE_DB_SECRET`: the same on every deploy and stored nowhere. `CREATE`/`ALTER ROLE` send only its SCRAM-SHA-256 verifier (`site_catalog.scram_verifier`), so the password never reaches a statement or a server log. Each role has `CONNECTION LIMIT 3` (the site's pool of 2 plus an overlapping deploy).
- **Row-level security keeps the sites apart.** The data tables have the policy `website_isolation`: `website_id = (SELECT catalog.current_website_id())`. That function maps `current_user` to an **active** website through `websites.db_role_name`, so a suspended or archived website's role sees no rows. A site can read its own `websites` row (policy `website_own_row`). `website_id` defaults to `current_website_id()` and `product_type_schema_id` to `catalog.item_schema_id(...)`, so the site's SQL never names either. RLS is not `FORCE`d: the owner `ai_codegen` and its members (the harness) bypass it to register and seed websites, as do superusers.
- **No row can point into another website**: child rows reference their parents through composite foreign keys that include `website_id` (a product's category, an image's product).
- **Grants to `webblock_site`**: `SELECT` on the reference tables and `websites`; `SELECT`, `UPDATE` on `business_profiles`; `SELECT`, `INSERT`, `UPDATE`, `DELETE` on `categories`, `products` and `product_images`; `SELECT`, `INSERT`, `DELETE` on `portal_revoked_sessions`. Sites cannot insert a profile, write `websites` or run DDL.
- **Storage accounting**: images and the logo are files in object storage; the rows keep only their storage key and size. The trigger `track_storage` (`SECURITY DEFINER`) adds every file's bytes to `websites.storage_used_bytes` and refuses a file over `max_image_bytes` (`ck_websites_max_image_bytes`) or past `storage_limit_bytes` (`ck_websites_storage_within_limit`); deleting an image gives its bytes back. `touch_row` sets `updated_at` and bumps `products.row_version`.
- **Reference data seeded locally**: the 8 onboarding business types (the `BusinessType` enum), each with the product type `item` and a version-1 `field_schema` of `{"type": "object"}`.
- **The site connects** through `lib/db.ts` with the libpq variables `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` (or `DATABASE_URL` for local work) and `search_path=catalog`; `currentWebsiteId()` reads its website. Without database settings it runs without a database. The pool has at most 2 connections, and every site's pool counts against this one server's `max_connections`.
- **Object storage** (`lib/storage.ts`, AWS Signature V4 with `node:crypto` only, path-style URLs) holds the files under `websites/<website_id>/<folder>/<uuid>.<ext>`. `/media/products/<id>` and `/media/logo` serve them (`lib/media.ts`) with a sandbox CSP and `nosniff`, and send anything that is not an image as an attachment. Without `SITE_STORAGE_*` the portal refuses uploads with a message, and new products can be added without a photo.
- **`db/schema.ts`** (`pgSchema("catalog")`) describes the catalog tables, the same for every site. It is a harness-owned template refreshed on every run (`scaffold.catalog_schema()`), so GLM cannot add tables, and a request's `drizzle_schema` no longer reaches the site.
- **`db/seed.sql`** holds the starting data: the business profile and products from onboarding, or for other projects the products in their `ProductCards` blocks. The seed is stored as data, never as SQL, and the script is rendered and escaped on every run. Its `catalog.`-qualified inserts take `website_id` from `current_setting('webblock.website_id')`. The profile insert is `ON CONFLICT (website_id) DO NOTHING`, and the categories (by slug) and products are inserted only while the website has no products, so seeding again never duplicates rows or overwrites the owner's edits. Prices are read as USD only (`seed_generator.price_value`): another currency (`45,000 KHR`, `€5`) or a non-number becomes NULL, which hides the price. An onboarding `logo_url` is dropped, because the catalog keeps only storage keys.
- With database settings, pages render at request time with the `products` rows. Without them, the site shows the Puck products and `/portal` reports that it is not set up.
- It meets Provisioning's runtime contract: a `Dockerfile`, a standalone server on port 8080, `/healthz` and `/readyz`, and a read-only file system ([section 10](#10-platform-integration-project-and-provisioning)).
- `/portal` lets the site's owners add, hide and delete products and edit the business details (the site only `UPDATE`s its profile). They sign in through WebBlock single sign-on (Keycloak), with no separate password. See [section 9](#9-product-portal-and-single-sign-on-keycloak). The page path `/portal` is reserved.

**Differences from the design:**
- Enum values are lowercase (`active`, `published`, ...), as in the design. This service's older local schemas use UPPERCASE; the difference is deliberate.
- Extensions for the owner portal: `business_profiles.maps_url`, `business_profiles.portal_edited_at`, and the table `portal_revoked_sessions` (primary key `(website_id, sid)`).
- The design gives the catalog to the Project Service; this service keeps a local copy, as it does for `project` and `provisioning`.
- The reference data (business types, product types, field schemas) is seeded locally by `007`; Project owns the real lists.

**What the `DEPLOY` step does** (`graph_pipeline._prepare_site_catalog`, only with `PUBLISH_TARGET=github`):
- Without `SITE_DB_SECRET` it logs `SKIPPED: [DEPLOY] site catalog (SITE_DB_SECRET is not set); the site deploys without a database.`
- Otherwise `site_catalog.prepare_site(project, seed_sql, business_type)`, in one transaction:
  0. pins `search_path = pg_catalog, pg_temp` and turns on `standard_conforming_strings`, each in its own statement (`site_catalog.SESSION_GUARDS`). The harness bypasses RLS here, on a database where site roles run their own code: a function or operator a site planted in `public` can't be picked up, and the seed's string literals are always read the way `seed_generator.literal()` escapes them;
  1. switches to `SITE_ROLE_ADMIN` (`SET LOCAL ROLE`) when it is set;
  2. creates the site's role (`CREATE ROLE ... LOGIN PASSWORD '<verifier>' CONNECTION LIMIT 3 IN ROLE webblock_site`), or re-keys it with `ALTER ROLE` and grants `webblock_site` if the role isn't a member;
  3. `RESET ROLE`;
  4. upserts the `websites` row. Its business type is the project's `business_type_id`, else the onboarding business type's code, else `everyday_store`. An existing website keeps its status and limits;
  5. runs `db/seed.sql` with `set_config('webblock.website_id', id, true)`;
  6. inserts a fallback `business_profiles` row (the project name and `SITE_TIMEZONE`), `ON CONFLICT DO NOTHING`;
  7. returns `PreparedSite(env, status)`.
- A database failure raises `SiteCatalogError`, which fails the step with `DEPLOY_FAILED`, because the site would otherwise go live without its data. On success it logs `Site catalog: role ... and website ready (status ...), seed applied.`
- A website that is not `active` logs `WARNING: [DEPLOY] the website is ... in the catalog, so the site gets no data.`
- Without object storage it logs `SKIPPED: [DEPLOY] object storage (SITE_STORAGE_* is not set); the portal can't upload images.`
- The database and storage settings (`site_catalog.site_env`) are merged with the SSO settings into the webhook's `site_env` ([section 9.8](#98-deploy-webhook-contract-site_env)).
- With `PUBLISH_TARGET=project` nothing provisions site data (as with SSO; [section 10.4](#104-not-aligned-yet-needs-the-other-teams)).

**What the deploy side must provide** (Provisioning Service / deploy provider; not done by this service): every `site_env` key from the webhook as an environment variable of the site ([section 9.8](#98-deploy-webhook-contract-site_env)). There is no schema or seed step to run.

**Limitation: fixed tables.** Every site uses the same catalog tables. A new table or column needs a new `sql/00N` migration in `catalog` (with `website_id`, the `website_isolation` policy and grants to `webblock_site`) plus the template `db/schema.ts`; `tests/test_site_catalog.py` checks the tables and columns against the design (`DESIGN`/`EXTENSIONS`).

**Checked on 30 Oct 2026** against the compose Postgres 16 only (production is still at `006`, so `007` is not applied there; a real S3/MinIO, portal uploads through a real sign-in and Docker builds were not part of this check):
- `007` applied twice cleanly.
- Two site roles were isolated: B could not see, change or forge A's rows, link A's category, write `websites`, insert a profile or run DDL. A suspended website got no rows.
- An image over the size limit and a file past the storage limit were refused (SQLSTATE 23514). Deleting a product returned its bytes, and deleting a website cascaded.
- `prepare_site` ran twice without duplicates, and the role logged in with the derived password (the SCRAM verifier works).
- `next build` passed with TypeScript. The site, run as its role, served products from the catalog and a detail page whose image came from object storage through `/media/products/<id>` (`image/png`, CSP, `nosniff`). `/readyz` answered 200, and 503 with a wrong password.
- The SigV4 signer matched AWS's published example, and a local S3 stand-in that verified every PUT, GET and DELETE with botocore accepted all signatures.
- No secrets appeared in the site's log.

---

## 7. Codegen Modes: `llm` vs `hybrid`

Set the mode for the whole service with `CODEGEN_MODE` in `.env`, or per request with the `codegen_mode` body field or the `X-Codegen-Mode` header. Both onboarding endpoints follow `CODEGEN_MODE` unless the request sets `codegen_mode`.

| Feature | `hybrid` mode (Default) | `llm` mode (LLM-First) |
| :--- | :--- | :--- |
| **Initial Generation** | Renders the Next.js site from the built-in templates. No GLM. | Renders the templates, then GLM reviews the block components for this site and changes only what improves it (often 1 or 2 files, sometimes none). |
| **Onboarding products** | Sample products for the business type. | GLM proposes products from the business description (`seed_source: "glm"`). |
| **Visual Puck Updates** | Rewrites `puck-data.json` only. A block type the site doesn't have yet is added from its template. No GLM. | Same, then GLM gets the Puck diff and updates the components where the change needs code. |
| **Natural Language Instructions** | On an existing site, GLM changes the code from the `instructions`. A new site ignores them. | GLM gets the instructions with the Puck layout or diff. |
| **When GLM fails** | Only instructions call GLM, so the run fails and can be sent again. | The run goes on with the code from before GLM ([section 1.2](#12-langgraph-state-machine--self-healing-loop)), except for instructions on an existing site. |

**How the harness calls GLM** (`app/services/glm.py`):
- **Streamed** (`GLM_STREAM=true`): the reply arrives piece by piece, so Cloudflare in front of the GLM server never sees a silent connection (it cuts those with HTTP 524 after about 100 s).
- **Thinking off** (`GLM_THINKING=false`): reasoning models spend most of their time thinking before they answer. Turning it off made code generation several times faster; the build and the fallbacks catch the mistakes thinking would have avoided.
- **Only the files GLM needs** (`glm.CONTEXT_SKIP`): files it may not change and the blocks don't use (the product portal and its sign-in, the seed, the theme stylesheet, docs) are left out, about 60% less text.
- **One retry** for a dropped connection or HTTP 429/5xx. Timeouts are not retried: a code request may take `GLM_TIMEOUT_SECONDS` (180 s), the product proposal 45 s.
- **Everything GLM writes is checked** (`validate_changes`: allowed paths, never harness-owned files, size limits), saved in the run's `SOURCE_ARCHIVE` and built in the Docker sandbox.

Measured on `glm-4.7-flash` (self-hosted, behind Cloudflare), creating a site in `llm` mode:

| | Before (thinking on, all files sent) | After |
|---|---|---|
| GLM tokens per code request (in / out) | 16,720 / 4,742 | 7,226 / 640 |
| Run time without the build (products + code) | 123 s, and 2 of 3 runs hit the 180 s limit | 16–19 s, 3 of 3 succeeded |
| Whole run with the Docker build | not measured | about 100 s (`npm install` 82 s, `next build` 5.5 s) |

---

## 8. Quick Start for Teammates

### Prerequisites
- Python 3.11+
- Docker (for PostgreSQL & isolated build validation)

### 1. Start Database & Run Migrations
```bash
docker compose up -d
```
*Starts PostgreSQL on port 5433. The `migrate` service then brings it up to date with Alembic: it runs the `sql/` files this database hasn't applied yet and records them in `public.alembic_version`.*

Alembic works for any database in your settings too (after `pip install -e ".[dev]"`):
```bash
alembic current               # which migration the database is at
alembic upgrade head --sql    # the SQL that would run, to review it or run it by hand (pgAdmin)
alembic upgrade head          # apply it
```
On production the login (`dev_svr`) can't create schemas, so set `DB_MIGRATION_ROLE=ai_codegen` (the database owner): Alembic switches to that role first. To add a migration, write `sql/00N_<name>.sql`, then run `alembic revision --rev-id 00N -m "<name in words>"`, which creates the revision in `migrations/versions/` that runs it.

### 2. Install & Start AI Service
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Start FastAPI server on http://localhost:8000
uvicorn app.main:app --reload --port 8000
```
*Access interactive OpenAPI documentation at [http://localhost:8000/docs](http://localhost:8000/docs).*

### 3. Run Automated Tests
```bash
# Make sure your virtualenv is active:
source .venv/bin/activate
pytest

# Or invoke directly via the virtualenv:
./.venv/bin/pytest
```
*All tests run offline except the three `*_not_found` tests in `tests/test_internal_routes.py`, which need the database from step 1.*

### 4. Try Business Onboarding End to End
With the API running (step 2), `scripts/e2e_onboarding.py` calls `POST /v1/onboardings` with a sample cafe (or `--business file.json`), prints the returned `db/schema.ts` and `db/seed.sql`, follows the run's steps, and downloads the generated Next.js + Drizzle source to `var/e2e/<project_id>/site`:
```bash
python scripts/e2e_onboarding.py                # onboard, show schema.ts + seed.sql, generate, download the site
python scripts/e2e_onboarding.py --serve        # ...then give the site its catalog role and website and run it on http://localhost:3100
python scripts/e2e_onboarding.py --mode llm     # GLM proposes the products and writes the code
```
Without `--mode`, the API's `CODEGEN_MODE` decides. The API needs `BUILD_RUNNER=docker` (Docker running) for a real build, or `BUILD_RUNNER=skip` to skip it. A healthy `llm` run prints `products: from glm`, `GLM (...) generated/updated N file(s)`, `status: SUCCEEDED` and, with `GITHUB_TOKEN` set, `github: https://github.com/<GITHUB_OWNER>/<repo>  commit: <sha>`. It takes about 100 s with the build. With GitHub configured, every run creates a real private repo.

To see the fallback, start the API with an unreachable GLM, for example `GLM_BASE_URL=http://127.0.0.1:9/v1 uvicorn app.main:app`, and run the script with `--mode llm`. The run still succeeds: `products: from template` and `WARNING: [AI_CODE_UPDATE] GLM failed, continuing with the code from before GLM: ...`.
`--serve` does what the platform does for a real site:
1. It builds the image from the site's `Dockerfile`, as Provisioning's Jenkins does.
2. It calls `site_catalog.prepare_site`, as the `DEPLOY` step does, against the compose database only (whatever `DATABASE_URL` the API uses), connected as the compose superuser, which may create roles: it creates the site's role `site_<id>`, registers its website in the shared `catalog` schema and applies the site's `db/seed.sql`. No database or tables are created per site.
3. It runs the image the way Kubernetes does: read-only root, only `/tmp` writable, UID 65532, port 8080, the `PG*` variables from `site_env`, logged in as the site's own role so row-level security applies.

It then checks `/healthz` and `/readyz`, and queries `catalog` to show the site's profile and products. It needs Docker, the compose Postgres at migration `007` (`docker compose up migrate`) and about 1 GB of free disk. Stop it with `docker rm -f wb-e2e-site`. With `GITHUB_TOKEN` set, start the API with `PUBLISH_TARGET=project` if you don't want a GitHub repo.

**Run end to end on 31 Oct 2026** (compose Postgres 16 at `008`, `CODEGEN_MODE=llm` with Qwen3-Coder-30B, `BUILD_RUNNER=docker`, a local stand-in deploy receiver; GitHub, Keycloak and object storage off): every step succeeded (`GITHUB_PUSH` and the SSO and storage parts of `DEPLOY` logged `SKIPPED:`). The webhook arrived signed with the `PG*` keys of `site_env`. The site image ran read-only as UID 65532: `/healthz` and `/readyz` 200, the home page and a `/products/<slug>` page served the 8 seeded products with USD prices, and the website was registered as `cafe`. The site role's password was in the webhook body only, not in the API log, the artifacts, the site log or the database. The run found two bugs, both fixed: the seed prompt asked for local currencies, which the USD-only catalog dropped, and `DEPLOY` read the seed from the wrong place, so every onboarded website became `everyday_store`. The same day, with GitHub on: the first run created the private repo `WebBlock-Organization/sok-coffee-24f58583` and pushed all 127 files to `main`, and two updates (a changed hero title, `llm` mode) each pushed one commit that changed only `components/blocks/Hero.tsx` and `puck-data.json`. That run fixed a third bug: picking up a request reset its edit revision to 0 in the commit message and the run log.

---

## 9. Product Portal and Single Sign-On (Keycloak)

Every generated site has a **product portal** at `https://<site>/portal`, where the site's owners add, edit, hide and delete the products the site shows. Owners never get a separate password: the portal uses **single sign-on (SSO)** with the same Keycloak realm as the rest of WebBlock. An owner who is already signed in to WebBlock clicks "Manage products" in the editor and lands in the portal without seeing a login screen.

This section explains how that works, what each service has to do, and how to work with it day to day.

### 9.1 The idea in one paragraph

Each generated site is its **own Keycloak client** (`webblock-site-<projectId>`) in the WebBlock realm. Keycloak handles the login, so the site never sees a password. The client has one client role, **`owner`**. Only users holding that role on that site's client may use its portal. The AI Code Service **creates and updates these clients automatically** in the `DEPLOY` step, so nobody creates them by hand, and it hands the site the settings it needs as environment variables. The Project Service decides **who** is an owner by calling three internal endpoints (section 9.6).

### 9.2 How sign-in works

```mermaid
flowchart TD
    Open["Owner opens /portal"] --> Cookie{"Has a valid<br/>portal session?"}
    Cookie -->|"yes"| Portal(["Product portal"])
    Cookie -->|"no"| Login["Site redirects to Keycloak"]
    Login --> Signed{"Signed in to<br/>WebBlock?"}
    Signed -->|"no"| Form["Keycloak login page"]
    Form --> Back
    Signed -->|"yes"| Back["Keycloak sends the owner<br/>back to the site"]
    Back --> Check{"Token valid and<br/>user is an owner?"}
    Check -->|"yes"| Portal
    Check -->|"no"| Denied(["Access refused"])
```

1. The owner opens `/portal`. With a valid portal session, they go straight in.
2. Without one, the site redirects to Keycloak.
3. Keycloak shows its login page only if the owner isn't signed in to WebBlock yet. Otherwise it sends them straight back, with no login screen.
4. The site checks the returned token and the `owner` role. It then opens the portal, or shows "Your WebBlock account cannot manage this site".

The session lasts one hour. After that the same silent round trip through Keycloak starts a new one. Every product change checks the session again. The individual checks are listed in section 9.5.

### 9.2.1 How sign-out works

```mermaid
flowchart LR
    Out["Owner signs out<br/>of WebBlock or the portal"] --> KC["Keycloak ends<br/>the session"]
    KC --> Notify["Keycloak notifies<br/>the site"]
    Notify --> Ended["Site marks the<br/>session as ended"]
    Ended --> Again(["Next visit to /portal<br/>asks to sign in again"])
```

Removing someone's `owner` role (section 9.6) takes effect at their next sign-in. Ending their Keycloak sessions as well makes it immediate.

### 9.3 The building blocks

#### How a site gets its Keycloak client

```mermaid
flowchart LR
    Deploy["Site is deployed"] --> Client["AI Code Service creates<br/>the site's Keycloak client"]
    Client --> Settings["Settings sent to the<br/>deploy receiver (site_env)"]
    Settings --> Ready(["Site can sign owners in"])
    Team["Project members change"] --> Owners["Project Service updates<br/>the owner role"]
    Owners --> Ready
```

No one creates the client by hand: every deploy creates or updates it. The Project Service keeps the list of owners up to date.

| Piece | Where | What it does |
|---|---|---|
| Keycloak admin module | `app/services/portal_sso.py` | Creates or updates the site's Keycloak client, its `owner` role and the `webblock_roles` claim mapper, and gives the project owner the role. It also grants, revokes and lists owners. Talks to the Keycloak Admin REST API with a service account. |
| `DEPLOY` step | `app/services/graph_pipeline.py` (`_ensure_portal_sso`, `deploy_node`) | Before every deploy: ensures the client exists and gets the site's SSO settings. After the deploy: registers the deploy URL as an allowed redirect too. If Keycloak is down, the deploy still goes ahead, the portal stays disabled, and the run log says why. |
| Deploy webhook | `app/services/deployment.py` | Sends the SSO settings as `site_env` to the deploy receiver, which must set them as the site's environment variables (section 9.8). |
| Internal endpoints | `app/api/routes/internal.py` | `GET/PUT/DELETE /internal/v1/projects/{id}/portal-owners[/{userId}]` for the Project Service (section 9.6). |
| Site: SSO logic | `lib/portal-auth.ts` (site template) | The site side of OpenID Connect: discovery, PKCE, token exchange, ID token verification (RS256/ES256 against Keycloak's published keys), the `owner` check, sealed cookies and back-channel logout. Uses only Node's built-in `crypto`, no extra npm package. |
| Site: routes | `app/portal/login`, `app/portal/callback`, `app/portal/backchannel-logout` | Start sign-in, finish sign-in, and receive Keycloak's "this session ended" calls. |
| Site: portal | `app/portal/page.tsx`, `app/portal/actions.ts` | The product screens. Every server action checks the session again, because a server action can be called without loading the page. |
| Site: data | `lib/products.ts`, tables `products` and `portal_revoked_sessions` | Product reads and writes. Writes check the owner session once more, so code added to the site later can't change products without a signed-in owner. |

All site files above are **harness-owned**: the harness rewrites them on every run, and GLM can't edit them, add files under `app/portal/`, or shadow them with a `.tsx` twin (`glm.validate_changes`). Existing sites get changes to them on their next update.

### 9.4 The Keycloak client the harness creates for each site

| Setting | Value | Why |
|---|---|---|
| Client ID | `webblock-site-<projectId>` (prefix: `KEYCLOAK_SITE_CLIENT_PREFIX`) | One client per site, so a leaked secret only affects one site. |
| Client authentication | On (confidential), client secret | The site exchanges the code on its server, so the secret never reaches the browser. |
| Flows | Standard flow (authorization code) only, with PKCE `S256` required | No implicit flow and no password grant. |
| Valid redirect URIs | `https://<site>/portal/callback`, one per known site URL | Exact URIs, never wildcards. Keycloak refuses any other redirect. |
| Post-logout redirect URIs | `https://<site>/` | Where the browser lands after "Sign out". |
| Back-channel logout URL | `https://<site>/portal/backchannel-logout`, session required | Keycloak tells the site when a session ends. |
| Client role | `owner` | Holders may manage this site's products. |
| Protocol mapper | "webblock site roles": this client's roles → ID token claim `webblock_roles` | The site reads the roles from the ID token. |

**Where the site URLs come from:** `SITE_URL_TEMPLATE` filled with the project's subdomain, plus the URL the deploy webhook reports back. New URLs are added and existing ones are kept. Remove an old domain from the client by hand in the Keycloak console.

**Run log lines** (in `run.log` during `DEPLOY`):
- `Portal SSO: Keycloak client webblock-site-… ready for https://…`: all good.
- `SKIPPED: [DEPLOY] portal SSO (KEYCLOAK_ADMIN_CLIENT_ID is not set)…`: SSO isn't configured on this service.
- `WARNING: portal SSO setup failed…`: Keycloak was unreachable or refused. The site deployed with its portal disabled, and the next deploy tries again.

### 9.5 Sessions, sign-out and security

| Topic | Behaviour |
|---|---|
| Session | Cookie `wb_portal_session`, scoped to path `/portal`, `HttpOnly`, `SameSite=Lax`, `Secure` in production. Encrypted with AES-256-GCM using a key derived from the client secret, so the browser can neither read nor change it. It holds only the user id, display name and Keycloak session id (`sid`), never tokens. |
| Lifetime | 1 hour. After that the site silently sends the owner back through Keycloak, which takes a second while they're still signed in to WebBlock. |
| Sign-out in the portal | Ends the portal session, then sends the browser to Keycloak's logout. Keycloak asks "Do you want to log out?", because the site keeps no ID token to skip that page. Confirming ends the Keycloak session, which signs the owner out of WebBlock too, and returns to the site's home page. |
| Sign-out anywhere else | Keycloak calls `/portal/backchannel-logout` with a signed logout token. The site verifies it and stores the `sid` in `portal_revoked_sessions`, and from then on that session is refused at once. |
| Removing an owner | Takes effect at their next sign-in, so within an hour at most. To cut access immediately, also end the user's sessions in Keycloak (Users → Sessions → Sign out), which triggers the back-channel logout. |
| Token checks | Signature against Keycloak's published keys (rotated keys are picked up automatically), issuer, audience = this client, `azp`, expiry with 60 s clock skew, `nonce`, `state` (constant-time compare), PKCE. |
| CSRF | Portal forms are Next.js server actions, which reject cross-origin posts. The session cookie is `SameSite=Lax`. |
| If something is missing | No SSO settings or no database settings: the portal says it isn't set up. The database can't be checked for ended sessions: the session is refused (fail closed). |

### 9.6 Who can manage a site: owners

- **First deploy:** the harness gives the `owner` role to the project's `owner_user_id`. **This must be the user's Keycloak user ID** (the `sub` claim).
- **Team changes:** the Project Service is the source of truth for project membership. When it changes, it calls:

```http
GET    /internal/v1/projects/{projectId}/portal-owners             → 200 [{"user_id": "...", "username": "...", "email": "..."}]
PUT    /internal/v1/projects/{projectId}/portal-owners/{userId}    → 204 (grant, idempotent)
DELETE /internal/v1/projects/{projectId}/portal-owners/{userId}    → 204 (revoke, idempotent)
```

`userId` is the Keycloak user ID (a UUID; anything else is a `422`). Errors:
- `404`: the site has no client yet, because it hasn't been deployed. Grant again after the first deploy.
- `502`: Keycloak failed.
- `503`: SSO is not configured on this service.

These are the only write endpoints under `/internal/v1`.

### 9.7 One-time setup

**A. In Keycloak (an admin does this once per environment)**

1. Use the realm in `KEYCLOAK_ISSUER_URI`, e.g. `webblock`. Its public URL must be reachable from every site, because sites fetch Keycloak's keys and exchange codes server to server.
2. Create a client `webblock-harness`:
   - Client authentication: **On**.
   - Standard flow: **Off**.
   - Service accounts roles: **On**.
3. On that client, open **Service account roles → Assign role → Filter by clients → realm-management** and assign **`manage-clients`**, **`view-clients`**, **`manage-users`** and **`view-users`**. The user roles are needed to give users the `owner` role and to list owners.
4. Copy the client secret from the **Credentials** tab.

**B. In this service's `.env`**

```ini
KEYCLOAK_ISSUER_URI=https://auth.example.com/realms/webblock
KEYCLOAK_ADMIN_CLIENT_ID=webblock-harness
KEYCLOAK_ADMIN_CLIENT_SECRET=<secret from step A4>
KEYCLOAK_SITE_CLIENT_PREFIX=webblock-site-
SITE_URL_TEMPLATE=https://{subdomain}.sites.example.com
```

With `KEYCLOAK_ADMIN_CLIENT_ID` empty, everything else still works: sites deploy, and their portal says it isn't set up.

`KEYCLOAK_ISSUER_URI` must be the realm's **public** issuer, exactly as its `/.well-known/openid-configuration` reports it: sites check every sign-in token's `iss` against it. If the Admin REST API is served only on a separate admin hostname (Keycloak's `hostname-admin`), set `KEYCLOAK_ADMIN_URL` to that host; the admin client's token still comes from the issuer. If that host sits behind Cloudflare Access, the Access application needs a **Service Auth** policy that includes the service token in `KEYCLOAK_CF_ACCESS_CLIENT_ID`/`_SECRET` (or the shared `CF_ACCESS_CLIENT_ID`/`_SECRET`); without it Cloudflare answers 403. The token is sent to the admin host only.

**C. The site catalog in this service's database (a DBA or ops, once per environment)**

The generated sites' data lives in the `catalog` schema ([section 6, Generated site database](#generated-site-database-and-product-portal)). On production (database `ai_codegen` on `10.109.2.186`; it is still at `006`, so these steps have not been done there yet):

1. Apply the migrations with `DB_MIGRATION_ROLE=ai_codegen`: `alembic current`, review `alembic upgrade head --sql`, then `alembic upgrade head`. Migration `007` (`sql/007_catalog.sql`) drops the empty `site_data` schema and creates `catalog`. Migration `008` (`sql/008_catalog_hardening.sql`) revokes `CREATE` on `public` from `PUBLIC` (the default only from Postgres 15) and makes `webblock_site` `NOLOGIN` where the migration role may; otherwise it raises a `NOTICE`. Check afterwards that `SELECT has_schema_privilege('public', 'public', 'CREATE')` is `false`.
2. Neither `ai_codegen` nor `dev_svr` has `CREATEROLE`, so `007` cannot create the group role and only raises a `NOTICE`. A DBA then:
   - creates the group, `CREATE ROLE webblock_site NOLOGIN;`, and re-runs the final `GRANT` block of `sql/007_catalog.sql` (if the group already exists from the `006` setup, `ALTER ROLE webblock_site NOLOGIN;` instead);
   - creates a `NOLOGIN` role-admin role the harness may switch to when it creates site roles:
     ```sql
     CREATE ROLE webblock_role_admin NOLOGIN CREATEROLE;
     GRANT webblock_site TO webblock_role_admin WITH ADMIN OPTION;
     GRANT webblock_role_admin TO dev_svr;
     ```
   - Then set `SITE_ROLE_ADMIN=webblock_role_admin` in this service's `.env`.
3. In this service's `.env`, set `SITE_DB_SECRET` (a long random value; changing it re-keys every site role on its next deploy, and sites not yet redeployed can no longer log in) and `SITE_STORAGE_*` (S3-compatible object storage for images). Set `SITE_DB_HOST` (and `SITE_DB_PORT`, `SITE_DB_NAME`) if sites reach the database by another address than `DATABASE_URL`'s, and `SITE_DB_SSLMODE` if it requires TLS.

**Connection budget:** each site role has `CONNECTION LIMIT 3` (its pool of 2 plus an overlapping deploy), and all of them count against this one server's `max_connections`, so size the server for the number of sites.

Without `SITE_DB_SECRET`, sites deploy without a database and show the products from their Puck data. Without `SITE_STORAGE_*`, the portal refuses image uploads.

**Known gaps** (security review, 31 Oct 2026):
- The object storage credentials in `SITE_STORAGE_*` are shared by all sites, so a compromised site could read, write and delete other sites' files. GLM-written code could also expose the key on a public page. Per-site storage credentials are recommended.
- Site roles share this service's database server: no `statement_timeout` or `temp_file_limit` per role yet, and by default they may `CONNECT` to the server's other databases and list every `site_<hex>` role in `pg_roles`. Restrict `+webblock_site` to this database in `pg_hba.conf`; a separate catalog database is the long-term fix.
- The template's `lib/db.ts` maps `PGSSLMODE=prefer` to no TLS, so set `SITE_DB_SSLMODE=require` where the network is not trusted.

**D. On the deploy side (Provisioning Service / deploy receiver)**

For each site:
1. Set every `site_env` key from the deploy webhook (section 9.8) as the site's environment variable. That includes the database settings (the site's own role and password) and, when configured, the `STORAGE_*` settings. There is no `npm run db:setup` any more: the harness creates the site's role and seeds it in `DEPLOY`.
2. Make the site reachable at the URL registered in Keycloak, over HTTPS in production.

### 9.8 Deploy webhook contract (`site_env`)

When the site catalog or SSO is configured, the deploy webhook body (`DEPLOYMENT_WEBHOOK_URL`, signed with `X-WebBlock-Signature` when `DEPLOYMENT_WEBHOOK_SECRET` is set) carries one more field, `site_env`: the database and object storage settings from `site_catalog.site_env` merged with the SSO settings from `portal_sso.ensure_site_client`. Each group is present only when it is configured (`SITE_DB_SECRET`; `SITE_STORAGE_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID` and `_SECRET_ACCESS_KEY` all set; portal SSO).

```json
{
  "event": "webblock.deploy",
  "project_id": "11111111-1111-1111-1111-111111111111",
  "release_id": "…",
  "repository_url": "https://github.com/org/site",
  "branch": "main",
  "commit_sha": "…",
  "site_env": {
    "PGHOST": "db.example.internal",
    "PGPORT": "5432",
    "PGDATABASE": "ai_codegen",
    "PGUSER": "site_11111111111111111111111111111111",
    "PGPASSWORD": "…",
    "PGSSLMODE": "prefer",
    "STORAGE_ENDPOINT": "https://s3.example.com",
    "STORAGE_REGION": "us-east-1",
    "STORAGE_BUCKET": "webblock-sites",
    "STORAGE_ACCESS_KEY_ID": "…",
    "STORAGE_SECRET_ACCESS_KEY": "…",
    "KEYCLOAK_ISSUER": "https://auth.example.com/realms/webblock",
    "KEYCLOAK_CLIENT_ID": "webblock-site-11111111-1111-1111-1111-111111111111",
    "KEYCLOAK_CLIENT_SECRET": "…",
    "SITE_URL": "https://sok-pottery.sites.example.com"
  }
}
```

- `PGPASSWORD`, `STORAGE_SECRET_ACCESS_KEY` and `KEYCLOAK_CLIENT_SECRET` are secrets: store them in your secret manager and never log them, or echo them in an error response. This service doesn't log them either: when the receiver answers with a non-2xx status, the start of its body goes into the run log and the run's `error_message`, with every `site_env` value whose key contains `PASSWORD`, `SECRET`, `TOKEN` or `KEY` replaced by `[redacted]` first (`deployment._redact`). The login role (`PGUSER`, `site_<project uuid hex>`) decides which website's rows a connection sees.
- `PGHOST`, `PGPORT` and `PGDATABASE` default to `DATABASE_URL`'s; `SITE_DB_HOST`, `SITE_DB_PORT` and `SITE_DB_NAME` override them.
- `SITE_URL` is missing when the URL isn't known before the deploy. The site then uses the request's host, and Keycloak still only accepts the registered redirect URIs.

### 9.9 Working with it

**Add a teammate to a site** (normally the Project Service does this):
```bash
curl -X PUT http://localhost:8000/internal/v1/projects/<projectId>/portal-owners/<keycloakUserId>
curl http://localhost:8000/internal/v1/projects/<projectId>/portal-owners
```

**Run a generated site locally with SSO.** Your local Keycloak needs the site's client, so deploy once with SSO configured, or create the client yourself as in section 9.4 with redirect URI `http://localhost:8080/portal/callback`. The site's role and website must already be in `catalog` (a deploy, or `scripts/e2e_onboarding.py --serve`, creates the role and registers and seeds the website). Then:
```bash
export PGHOST=localhost PGPORT=5433 PGDATABASE=webblock PGSSLMODE=disable
export PGUSER=site_<project uuid hex> PGPASSWORD=<the site_env of its deploy, or site_catalog.role_password(project_id)>
export KEYCLOAK_ISSUER=http://localhost:8180/realms/webblock
export KEYCLOAK_CLIENT_ID=webblock-site-<projectId>
export KEYCLOAK_CLIENT_SECRET=<that client's secret>
export SITE_URL=http://localhost:8080
npm install && npm run dev   # then open http://localhost:8080/portal
```

**Change the portal or the sign-in code:**
- Edit the templates in `app/templates/site/` (use the `update-site-template` skill). Every existing site receives the change on its next update, because these files are in `scaffold.REFRESHED_TEMPLATES`.
- Keep `ROLES_CLAIM`/`OWNER_ROLE` in `lib/portal-auth.ts` in sync with `portal_sso.py`.
- Keep the `portal_revoked_sessions` queries in `lib/portal-auth.ts` in sync with the table in `sql/007_catalog.sql` (keyed `(website_id, sid)`) and the template `db/schema.ts`.
- Unit tests: `pytest tests/test_portal_sso.py`. They cover the Keycloak admin flow against an in-memory fake Keycloak, the internal endpoints and the webhook.
- Then build a generated site with `next build` (the `update-site-template` skill shows how).

### 9.10 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Portal says "not set up yet" | The site lacks `KEYCLOAK_*` or its database settings | Check the run log's `Portal SSO:` line and that the deploy receiver set `site_env`. |
| Keycloak page "Invalid parameter: redirect_uri" | The site's URL isn't registered on its client | Set `SITE_URL_TEMPLATE`, or redeploy so the deploy URL is added. Or add the URI in the Keycloak console. |
| "Your WebBlock account cannot manage this site" | The user lacks the `owner` role on `webblock-site-<projectId>` | `PUT /internal/v1/projects/{id}/portal-owners/{userId}`. Check that `owner_user_id` is the Keycloak user ID. |
| "Signing in did not work" | Token exchange or token check failed: wrong client secret, clock skew over 60 s, the site can't reach Keycloak, or the Keycloak session had already ended | Look for `[webblock] Portal sign-in` lines in the site's logs. |
| Run fails in `DEPLOY` with `Site catalog setup failed: …` (`DEPLOY_FAILED`) | The `catalog` schema is missing (`007` not applied), the harness may not create the site's role (`webblock_site` missing, or no `CREATEROLE`: set `SITE_ROLE_ADMIN`), or the seed failed | Apply the migrations and the DBA steps (section 9.7 C), then send the generation again. |
| `/readyz` answers 503, site log `readyz: not ready (code 28P01)` | Wrong password for the site's role, e.g. `SITE_DB_SECRET` changed since its last deploy | Redeploy the site, which re-keys its role. |
| `/readyz` answers 503, site log `readyz: not ready (code UNKNOWN_WEBSITE)` | The role maps to no active website: not registered, or its `websites.status` is `suspended`/`archived` (the run log then has `WARNING: [DEPLOY] the website is ...`) | Redeploy, or set the website back to `active`. |
| Run log `WARNING: portal SSO setup failed` | The admin client lacks the realm-management roles, or Keycloak is unreachable from this service | Fix the service account roles (section 9.7 A3). The next deploy retries. |
| A removed owner can still edit | Their portal session is still within its hour | End their sessions in Keycloak (Users → Sessions), which triggers the back-channel logout. |

### 9.11 Not done yet

- Deleting a project does not delete its Keycloak client. Delete `webblock-site-<projectId>` in the console for now.
- Domains that a site no longer uses stay registered on its client until someone removes them by hand.
- The editor's "Manage products" button belongs to the frontend repo. It only needs to open `https://<site>/portal`.

---

## 10. Platform Integration: Project and Provisioning

In the WebBlock platform this service never talks to Provisioning. The chain is:

```
Project ──project.generation_requested.v1──▶ AI Codegen ──ai_codegen.generation_completed.v1──▶ Project
          (reads codegen-input, sec. 5)        generates + builds          (reads the run, downloads the source, sec. 3)
Project ──commits to Gitea, project.source_published.v1──▶ Provisioning ──Jenkins builds the Dockerfile──▶ Kubernetes
```

The sources are the Project Service's `.claude/docs/contracts/service-communication.md` (section 2 is AI Codegen's contract) and the Provisioning Service's `README.md` ("Contract for generated sites", its open decision OD-64).

### 10.1 Turning it on

| Setting | Value on the platform |
|---|---|
| `PUBLISH_TARGET` | `project`. `GITHUB_PUSH` and `DEPLOY` log `SKIPPED:` (Project commits; Provisioning deploys), and the run ends with its acceptance and the completion event. `github` (default) keeps pushing to GitHub and calling the deploy webhook, for local demos. |
| `RUN_CONSUMER` | `true`, with `RABBITMQ_*` pointing at the platform broker |
| `PROJECT_SERVICE_URL` | The Project Service's in-cluster URL |
| `ARTIFACT_URL_SECRET`, `ARTIFACT_BASE_URL` | A random secret, and this service's in-cluster URL, so Project can download a run's artifacts |
| `CODEGEN_MODE` | `hybrid` or `llm`. Project's input carries no mode or AI instructions. |

### 10.2 What the generated site provides (Provisioning's runtime contract)

| Requirement | How the site meets it (`scaffold.RUNTIME_PATHS`, harness-owned, refreshed into existing sites on every run) |
|---|---|
| A `Dockerfile` at the repository root, `linux/amd64`, no database or secrets in the build | A three-stage build on `node:22-alpine` pinned by digest: `npm ci --ignore-scripts` from the committed `package-lock.json`, then `next build`, then a runtime stage with only `node`. Every page that reads data renders at request time (`connection()`, `force-dynamic`). |
| HTTP on `0.0.0.0:8080`, UID/GID 65532, a read-only root with only `/tmp` writable | `output: "standalone"` and `node server.js`, with `PORT=8080`, `HOSTNAME=0.0.0.0` and `USER 65532:65532`. Next.js's cache directory is a link to `/tmp/next-cache`. |
| `GET /healthz`: 200 without touching any dependency | `app/healthz/route.ts` |
| `GET /readyz`: 200 only when a query as the site's own role answers within a few seconds, otherwise 503; no redirect or authentication, a tiny body | `app/readyz/route.ts` calls `lib/products.ts` `catalogIsReachable`: `SELECT 1 FROM products`, then a check that the site's role maps to an active website (`current_website_id()`, `UNKNOWN_WEBSITE` otherwise), within 2 s. The log carries the error code only. |
| Database from `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` | `lib/db.ts` only, logged in as the site's own role (`site_<project uuid hex>`) with `search_path=catalog`; no other database variable is needed. Object storage comes from the extra `STORAGE_*` variables in `site_env` (`lib/storage.ts`). `PGSSLMODE` follows libpq: `disable`/`allow`/`prefer` connect without TLS, `require` encrypts, `verify-ca`/`verify-full` verify the server. The pool has at most 2 connections. |
| No `X-Webblock-Release` header, no fixed hostname | The site sets neither. `/healthz`, `/readyz` and `/portal` are reserved page paths. |

Checked on 30 Oct 2026, with the earlier `site_data` database (Docker builds were not repeated after the move to `catalog`; that check ran the site as its role without Docker, [section 6](#generated-site-database-and-product-portal)), by building the image the way Jenkins does (`docker build --platform linux/amd64`) and running it the way Kubernetes does: `--read-only --tmpfs /tmp --user 65532:65532 --cap-drop ALL`.
- `/healthz` answered 200 with or without the database.
- `/readyz` answered 200 with the database, and 503 with a wrong password or without one.
- The home page showed the products from the database.
- Writing to `/app` failed (read-only), and the password never appeared in the logs.

### 10.3 The handoff, checked

On 30 Oct 2026 a stand-in for Project's `codegen-input` endpoint served its envelope and page format. The `generation_requested` handler was called directly with Project's event body, because RabbitMQ would not start on the test machine; the AMQP layer is covered by `tests/test_event_bus.py`. The results:
- The run was queued once. A second delivery while it ran did nothing, and a third after it finished re-published the completion (still one run).
- The run succeeded with `GITHUB_PUSH` and `DEPLOY` skipped.
- `GET /internal/v1/generation-runs/{id}` returned `inputHash` equal to Project's `capturedInputHash`, and all three artifacts downloaded through their signed URLs with matching SHA-256.
- The source archive, built and run as above, served the site from its database.

### 10.4 Not aligned yet (needs the other teams)

| Gap | Platform design | Here | Owner |
|---|---|---|---|
| The site's database | The shared `catalog.*` tables (Project's Flyway migrations), one `site_<id>` role per site with row-level security, and no DDL | The same design (tables, per-site roles in the group `webblock_site`, `website_isolation` policies, no DDL), but as a local copy in this service's database (`sql/007_catalog.sql`). The harness creates each site's role, registers its website and seeds it in `DEPLOY`, only with `PUBLISH_TARGET=github`, and seeds the reference lists itself | Phase 3: agree with Project on the location (Project-owned schema vs this local copy) and who registers and seeds each website |
| Portal sign-in | No per-site Keycloak client: oauth2-proxy at the gateway with the `site-portal` client, then Project's `manage_site` check | A Keycloak client per site (section 9) | Phase 4, once oauth2-proxy runs on the cluster |
| Output shape | An overlay inside the scaffold's `allowedPaths` (Project's J4 `scaffold` endpoint and `limits`) | The complete site | Project: until J4, accept the complete site? |
| Component catalog | Project's `componentCatalogVersion` | This service's `catalog.py` blocks | Agree on one catalog |
| Outbox | Events written in the same transaction as the change | Best-effort publish; Project's deadline recovers a lost completion | This service, later |
| Event schemas | Pinned copies from `web-block-contracts` | Written from Project's doc and its Java models | Get read access to `web-block-contracts` |

