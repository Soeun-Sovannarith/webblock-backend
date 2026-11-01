# 0015: DevOps agent for failures outside the worker's control (proposal)

Status: **proposed; nothing decided or implemented.** Lines marked *(owner)* are the owner's statements; everything else
is the lead's proposal and needs the owner's decision. Section 5 proposes a database change: per `0014` section 2,
nothing is added until the owner approves it.

## 1. Problem

The worker already keeps the live site safe when a deploy or promotion fails for reasons outside its control: the
candidate is deleted, the route is restored or never moved, and the command ends `failed` with a failure code. Three
things are missing:

1. **Diagnosis.** Several different causes end with the same code:
   - pods that cannot be scheduled (not enough CPU or memory);
   - a ResourceQuota that refuses new pods;
   - a node or network problem.

   All three time out as `progress_deadline_exceeded`.
2. **Notification.** A failed command waits in `commands`; nobody is told.
3. **Repair.** Nobody acts until a person happens to look.

## 2. The owner's direction

- A **local AI agent for DevOps**: woken by a message on RabbitMQ when the system fails, it checks the status, finds the
  root cause and fixes it. When a fix needs a human, it messages the admin (human in the loop).
- The **harness** is an open-source agent harness, augmented: Kimi Code or another one. It lives **outside `src/`**.
- The **model** is a local coding model, such as SERA-14B or another model built for coding agents.
- **Observability** already exists: Prometheus, Grafana and Loki are set up, and Sentry is planned for errors.

### Decided after the research

The research report `reports/Local DevOps agent harness and model.md` compared harnesses and 14B to 20B models. The
owner decided:
- **One model for both layers: gpt-oss-20b** (21B total, 3.6B active, Apache-2.0, 128K context). The owner counts it as
  "around 20B".
- **A different harness for each layer:**
  - **Diagnosis layer: HolmesGPT.** It reads cluster state, metrics, logs and Sentry, follows the runbooks and proposes
    a fix, with tool approval switched on. It has no tool that changes anything.
  - **Coding layer: opencode**, driven through its HTTP server. It drafts a code or configuration fix as a patch, in a
    disposable working copy, for a human to review. It holds no cluster credential and pushes nothing; a merged fix
    reaches production only through the normal release path (Project → build → deploy → promote).
- **SERA-14B is not used**: it was validated only on Python SWE-bench, has no safety tuning, and has no official
  quantized builds.
- **Where the model runs:** production gets its own server. **For testing effectiveness** (the evaluation of section 7),
  the model is served on a Modal.com account used only for that test environment.
  - The endpoint is OpenAI-compatible and needs authentication: a Modal proxy-auth token or an API key. It is never left
    open.
  - A 24 GB GPU class is enough for gpt-oss-20b. llama.cpp (`llama-server --jinja`) runs on any of them; whether vLLM's
    gpt-oss support covers the chosen class is checked first.
  - The containers scale to zero between runs, and a run keeps one warm for its duration.
  - **Data:** the evaluation sends Modal only prompts built from the Kind cluster's test data, with tool output scrubbed
    of secrets by the bridge. No customer content and nothing from the real cluster.
- **The open items stay open:** the database option, the approval channel, Sentry hosting, tier 1, and the real cluster
  (section 9).

## 3. Proposed shape

```
Provisioning (src/)                          ops-agent/ (separate package, own lock file)
  worker records the failure  ──outbox──▶ RabbitMQ ──▶ bridge ──▶ harness run (headless)
  Alertmanager (cluster-wide alerts) ──webhook──────▶ bridge        │ MCP tools, by tier
                                                       ▲            ▼
                                     admin approves ───┘   diagnosis, proposed fix, or action
```

- **Provisioning stays as it is**, except for steps 4.1 and 4.2 below. The agent is a separate consumer and never part
  of the command path. Provisioning never waits for it.
- **The bridge** is a small service, not an agent. It:
  - consumes failure events and Alertmanager webhooks;
  - removes duplicates and limits how often it starts a run per project and per hour;
  - starts one headless harness run with a fixed prompt and the event's context;
  - stores the result;
  - sends the admin the diagnosis;
  - holds pending approvals.
- **The harness** runs the model with MCP servers as its only tools (section 6), under a written runbook per failure
  code (`ops-agent/runbooks/`).

### Folder

```
ops-agent/
  README.md            what it does, how to run it, its limits
  bridge/              Python service: RabbitMQ and Alertmanager in, harness runs, admin messages, approvals
  harness/             harness configuration: agent spec, system prompt, MCP server list, permission rules
  runbooks/            one file per failure code: what to check, which fixes are allowed
  deploy/              its own ServiceAccount and RBAC (read-only), container or compose files
  evals/               recorded failures and the expected diagnosis (section 7)
  tests/
```

It has its own `pyproject.toml` and `uv.lock`, and its own make targets (`ops-*`). Nothing in `src/` imports it, and it
imports nothing from `src/`. It talks to Provisioning only through the published REST/MCP API and the event. *(Owner,
[`0019`](0019-service-identity-is-the-mesh.md) section 5: the agent's `AuthorizationPolicy` row on Provisioning's API,
for its ServiceAccount on `/v1/*` and `/mcp` and still with a user's JWT, is added only when this proposal is accepted;
until then the API's sidecar refuses it.)*

## 4. Steps

1. **Precise failure codes in Provisioning** (no database change; the failure columns exist):
   - `insufficient_resources`: pod `Unschedulable`;
   - `quota_exceeded`: ReplicaSet `FailedCreate` naming a quota;
   - `dependency_unavailable`: the preview probe answers 503;
   - `cluster_unavailable`: already exists.

   A short, cleaned-up `failure_summary` goes with each one, with no object values, secrets or customer content. This is
   useful to humans even without an agent, and could join task T.
2. **A failure event** (section 5): written in the same transaction as the failing write, then published to RabbitMQ
   with publisher confirms. This is the same rule as command dispatch (`0005`).
3. **The bridge and a read-only agent:** it diagnoses and messages the admin, and takes no actions.
4. **Allowed actions:** add the tier 1 actions of section 6 only after the eval set (section 7) shows the diagnoses are
   right often enough.


**Step 1 implemented**. `core/domain/values/failure_codes.py` is the one catalogue of every code a
release, step or command can record, with its places, meaning and `retryable` flag. A unit test scans the package and
fails on an uncatalogued written code or an unwritten entry, and `.claude/docs/runbooks/failure-codes.md` shows the same
tables.
- **`insufficient_resources`** (a candidate pod `Unschedulable`) and **`quota_exceeded`** (the Deployment's
  `ReplicaFailure`/`FailedCreate` naming a quota, read from the condition's reason and the phrase only; the message is
  never kept) replace `progress_deadline_exceeded` at the progress deadline. Seen earlier, they replace
  `candidate_not_ready` at the attempt's timeout and never end an attempt early. The outcome is unchanged.
- **`dependency_unavailable`**: the candidate's preview probe answered 503 with its own release header while the
  candidate has a ready endpoint (`SiteDependencyUnavailable`, an additive port exception). The ready-endpoint condition
  exists because Cilium's Envoy answers its own 503 for a backend without a ready endpoint, and that reply carries the
  route's header too (found live). Residual: an Envoy-generated 503 while an endpoint is ready (a connect failure or
  reset) can still be classed `dependency_unavailable`; both codes are retryable. The promotion fails as before, with
  that code, on the command and, for a resumed promotion that restores the route, on the release.
- **`cluster_unavailable`** stays a dispatch-row code.
- The build system's codes are kept only when catalogued; anything else is stored as `build_failed`.
- The API returns `failure_retryable` exactly where it returns a failure code: an additive REST field, and named in the
  MCP tool descriptions (no MCP `outputSchema`).
- **Approved by the owner** (`0016` section 7 item 4): the `retryable` flags, including the judgment calls
  `image_pull_failed` (yes), `pipeline_failed` (yes), `progress_deadline_exceeded` (yes), `quota_exceeded` (no) and
  `executor_failed` (no).
- **Not done:** `failure_summary` needs a column or the step 2 outbox, both schema changes. It awaits the owner.

## 5. Proposed database change (needs the owner's approval)

**Option A (recommended): an operational outbox in `provisioning_ops`.** This follows `0005` and `0008`: operational
storage is separate from the four business tables, and is not added to them.

| Column | Type | Notes |
| --- | --- | --- |
| `event_id` | uuid PK | |
| `project_id` | uuid | |
| `release_id` | uuid, nullable | FK to `releases (project_id, release_id)` |
| `command_id` | uuid, nullable | FK to `commands` |
| `kind` | enum `ops_event_kind` | `command_failed`, `release_failed`, `route_uncertain` |
| `failure_code` | varchar(100) | the same code the business row records |
| `diagnostics` | jsonb, at most 8 KB | cleaned-up facts: conditions, reasons, counts; never values, secrets or logs |
| `occurred_at` | timestamptz | |
| `next_publish_at`, `publish_attempts`, `published_at` | as `command_dispatch` | the dispatcher's retry state |

- **Unique keys:** `(command_id, kind)` and `(release_id, kind)`, so a retried write records one event.
- **Who writes:** the worker writes in the same transaction as the failing write, through the fenced store. The build
  store writes for a failed release.
- **Who publishes:** the existing dispatcher pattern, to exchange `webblock.provisioning.ops`, routing key `ops.<kind>`.
- **Roles:** INSERT for `provisioning_worker` and `provisioning_build`. UPDATE of the publish columns for the
  dispatcher's login. No other role gains anything.
- **Retention:** rows older than 30 days after `published_at` are deleted by a job. That is the only delete in
  Provisioning's schemas, so it needs its own ruling.

**Option B (smaller): no new table.** A new read-only role `provisioning_ops_reader` may `SELECT` failed rows of
`commands` and `releases`, and the bridge polls them with its own cursor.
- **Pros:** no outbox and no publisher.
- **Cons:**
  - it polls instead of being woken;
  - the diagnostics are limited to `failure_summary`;
  - the bridge needs a database login into Provisioning's schema.

## 6. What the agent may do (tiers)

| Tier | How it runs | Examples |
| --- | --- | --- |
| 0, read | automatically | Grafana MCP (PromQL, LogQL), Sentry MCP (issues of one project), Kubernetes MCP read-only (get, describe, events, logs in `wb-*`; node and quota status), Provisioning MCP reads (`get_release`, `get_command`, `get_deployment`, `get_release_diagnostics`) |
| 1, gated actions | automatically, within limits | only Provisioning's own MCP tools, which already enforce every rule: `build_release` again for a build that failed transiently; `deploy_release` again after a transient cluster error. At most one automatic retry per release, and only for codes the runbook marks transient |
| 2, human in the loop | admin approves first | raise a quota, add or resize nodes, change a NetworkPolicy, operator `rollback` of an uncertain route. The agent sends the diagnosis and the exact proposed change; the bridge holds it until the admin approves |
| Never | refused by its identity | reading Secrets, writing to the database, deleting namespaces or releases, touching the real cluster without the owner's explicit configuration (`.claude/CLAUDE.md` section 0) |

Its Kubernetes identity is a separate ServiceAccount `webblock-devops-reader` with read-only access and no Secrets. Any
write it is ever given goes through an admission policy like `webblock-bootstrap-scope`.

## 7. Model and harness

> Superseded in part by section 2, "Decided after the research": gpt-oss-20b for both layers, HolmesGPT for diagnosis,
> opencode for coding. The comparison below is kept as history; the full evidence is in the research report.

**Model.** Diagnosis is mostly tool use and reading metrics and logs, not repository editing. So reliable tool calling
and context length matter more than SWE-bench scores.

| Model | Size | For | Against |
| --- | --- | --- | --- |
| **Qwen3-Coder-30B-A3B** (recommended to start) | MoE, about 3B active | fast, strong tool calling, long context; fits a 24 GB GPU or 32 GB of unified memory | needs more memory than a 14B model |
| **Devstral Small 2** | 24B dense, Apache-2.0 | built for agent harnesses; one 24 GB GPU | slower than the MoE |
| **SERA-14B** (Ai2, Apache-2.0) | 14B, Qwen3-14B base | smallest; open data and method | 32K context; validated only on SWE-bench (Python repositories); released without safety tuning; trained to fix code, not to diagnose clusters |

**Hardware.** None of these runs beside the Kind stack on this 15 GB machine. The model needs its own machine or GPU.
The harness only needs an OpenAI-compatible URL.

**Harness candidates**, compared in a one-day spike:

| Harness | Notes |
| --- | --- |
| **Kimi Code CLI** (the owner's first choice) | MCP, headless `-p`, hooks and skills. The older Python Kimi CLI is archived. Kimi Code is the TypeScript successor; whether it runs against a local OpenAI-compatible endpoint must be confirmed in the spike |
| goose | MCP-native, runs local models through OpenAI-compatible providers, headless recipes |
| OpenHands | any LiteLLM provider, Devstral was co-designed with it, heavier |

**Spike acceptance:**
- runs headless against the local model;
- uses only the MCP servers it is given;
- refuses unapproved shell and file writes;
- produces a structured result the bridge can parse;
- is kept to one pinned version.

**Evals.** Before tier 1 is switched on, induce each failure on Kind and record it. The failures:
- not enough CPU;
- quota exceeded;
- image pull failure;
- crash loop;
- catalog database down;
- API server unreachable;
- Gateway down.

Measure, per model and harness:
- how often the diagnosis is right;
- how often the proposed fix is right;
- whether it ever tried something outside its tier.

## 8. Observability *(the owner's existing stack)*

- **Prometheus:**
  - Provisioning exposes `/metrics`: commands by kind, status and failure code, build and deploy durations, dispatch
    lag. This needs `prometheus-client`, a new dependency the owner approves.
  - kube-state-metrics alerts for node pressure, unschedulable pods and quota near its limit go to Alertmanager, whose
    webhook wakes the bridge for cluster-wide problems.
- **Loki:** Provisioning's JSON logs are shipped as they are (structlog, no secrets). The agent queries them with LogQL
  through the Grafana MCP server, under a Viewer service account.
- **Sentry:** Provisioning reports exceptions with the Sentry SDK (a new dependency). The agent reads issues through
  Sentry's MCP server with a read-only token. `.claude/CLAUDE.md` section 3 applies: data exposed to agents is scoped to
  a project and cleaned up.
- **Grafana:** one dashboard for the bridge (runs, outcomes, approvals pending), plus the Provisioning dashboard.

## 9. Open questions for the owner

1. Option A or B for section 5.
2. Where the admin is messaged, and how an approval is given (Slack, Telegram, e-mail, a web page).
3. *(Answered for testing: Modal.com; production: the owner's own server, still to be sized.)* Which machine runs the
   model. The research recommends one 24 GB NVIDIA GPU (a used RTX 3090 or an RTX 4090) in a host with at least 32 GB
   RAM, serving gpt-oss-20b with `llama-server --jinja` for both harnesses.
4. Whether Sentry is self-hosted or SaaS: it decides where its data lives.
5. Whether tier 1 is ever wanted, or the agent stays advisory (tiers 0 and 2 only).
6. When the agent may see the real cluster. Until the owner names it in `.claude/CLAUDE.md` section 0, it is Kind only.

## 10. A System 1 model as an escalation gate: Laya *(owner; review `ai/reviews/devops-overseer-review.md`)*

**Decision.** Before the agent carries out any proposed action, a "System 1" decision model judges whether the action
could harm the cluster. If the model judges it harmful, or is unsure, the action is **not** carried out and an **ALERT**
goes to the admin, who decides. The model never runs anything itself.

The owner first named TypeSafe AI's Jev, then chose **Laya** (`convaiinnovations/laya` on Hugging Face, Convai
Innovations, Apache 2.0) instead.

**Laya, from its model card (the claims are the authors', not verified here):**
- **What it is:** a non-autoregressive classifier of about 0.4B parameters (ModernBERT-large with a decision head). It
  takes a state (text or JSON) plus typed questions (`choice`, `score`, `noul`), and returns typed answers with
  probabilities and a `confidence`. It generates no text.
- **How it runs:** the weights run locally (`laya` Python package, or the self-hosted `laya-serve` HTTP server). The
  model card cites about 33 ms per question on a T4 GPU and about 0.2 to 0.5 s on CPU. **Nothing leaves our
  infrastructure**, which settles the data-egress question the Jev variant raised.
- **Limits the card states, which shape the rules below:**
  - the base checkpoints are near chance on typed decisions zero-shot: 0.362 accuracy against a 0.461 majority baseline;
    0.766 after fine-tuning;
  - the model ships over-confident, with a mean ECE of 0.466 before its temperatures are refitted on your own data;
  - `noul` (yes/no) answers can follow the option labels rather than the input (issue #156);
  - `action.act_probability` carries no usable signal yet (issue #185); the card advises gating on `confidence`;
  - the root checkpoint is English only, with a 512-token context.

**Rules that keep the gate sound:**
1. **Escalate-only.**
   - The gate can stop an action and alert the admin. It can never allow an action that the deterministic controls
     refuse.
   - Those controls are: the agent's read-only RBAC and its deny-writes admission policy; every change going through
     Provisioning's typed, fenced commands; rate limits and budgets; and human approval for tier 2.
   - The order is: the deterministic checks first; the gate only on actions that already passed them.
   - A weak gate therefore costs false alerts, not cluster safety.
2. **Fail closed.** A timeout, an error, or a `confidence` below the threshold means the action is held and the admin is
   alerted. The gate uses `confidence`, never `act_probability`.
3. **Trusted, short inputs only.** The gate receives a compact JSON within 512 tokens:
   - the proposed typed action (kind, project, target, parameters);
   - structured platform state from Provisioning's own records (route state, command state, the release's failure code,
     quota and node figures).
   It never receives raw tenant logs, events or `/readyz` bodies (prompt injection).
4. **Typed questions as `choice`, not `noul`.**
   - The harm questions use explicit, input-specific options, because of the card's `noul` label-bias warning. For
     example: "what can this action affect: the named release objects only / other objects in the project / objects
     outside the project / cluster-wide".
   - Reversibility is a separate question, and so is "touches capacity, quota, network or identity".
   - Any answer other than the safe option, or a low confidence, means the action is held and the admin is alerted.
5. **Fine-tuned and calibrated before use.**
   - Use the `laya-typed-decisions` checkpoint, fine-tuned on our own labelled set: the QA failure catalogue, the
     agent's tool set with safe and harmful variants, and planted injection cases.
   - Then refit its temperatures on held-out data from the same set.
   - The base checkpoint is never used as the gate.
6. **Pinned and measured.**
   - The checkpoint is pinned by its Hugging Face revision hash, and the runtime by version. They are served by
     `laya-serve` beside the bridge.
   - The gate first runs in shadow mode against the QA catalogue on Kind and against the planted cases. The owner sees
     its catch rate on harmful actions and its false-alert rate, and the threshold is set from that run.
7. **The alert** names the proposed action, the gate's answers and confidences, and the agent's diagnosis. The admin
   channel is open question 2 of section 9.
8. **Audited.** Every gate decision (action, answers, confidences, outcome, checkpoint revision) is an audit event, in
   the same structured format as Provisioning's (`0014` section 21, answer 3).

**Open (added to section 9):**
- 7. Who labels the fine-tuning and calibration set, and how large it must be before shadow mode starts.
- 8. Whether the gate runs on the model host's GPU or on CPU. CPU, at about 0.5 s per decision, is likely enough.

## 11. Local hosting with about 30 GB of VRAM; GLM-4.7-Flash as the challenger

- **Hosting.** The agent's model is hosted locally on the owner's hardware, with a VRAM budget of about 30 GB. Modal.com
  stays possible for the test phase but is not available yet.
- **The pick stays gpt-oss-20b** (section 2). In native MXFP4 its weights take about 13 to 14 GB, which leaves room for
  long contexts.
- **Challenger: GLM-4.7-Flash** (Z.ai, MIT according to secondary sources; about 30B MoE with about 3B active; vendor
  τ²-bench 79.5%). It runs at 4-bit (about 18 to 19 GB of weights). 8-bit does not fit. It replaces Qwen3-Coder-30B-A3B
  as the second model of the section 7 evaluation.
  - Check its official model card and license, and whether a newer open-weight GLM Flash exists, before the evaluation
    starts.
- **One model at a time.** Both together exceed 30 GB, so the evaluation runs them one after the other on the same Kind
  failure set.
- **The usable context at 30 GB is measured, not assumed.** The evaluation records the largest context that fits for
  each model and quantization, and the result shapes how much log the agent reads per pass.
- **The Laya gate** (section 10) is about 1 GB. It fits beside either model, or runs on CPU.
- **The evaluation decides.** Its measures are the right diagnosis, the right proposed fix, any attempt outside the
  agent's tier, and speed. The winner becomes the model of both layers. The harnesses (HolmesGPT, opencode) and the gate
  do not change.
