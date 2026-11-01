# Final review: cqrs-layout python..718b4a3

Verdict: needs a fix wave (one small contract text task), then ready with listed carry-overs once the owner rules
on the items under "Owner rulings before the merge".

Stage: 5 Verify. Base `python` at `5e7a757` (the merge base), head `718b4a3`, 114 commits, 628 files. IDs covered:
TR-2, TR-54, TR-55 (decision `0025`), TR-8, TR-9 (decision `0026`), FR-50 and TR-48 (rollback is `ADMIN` only),
the spec changes of FR-19, FR-21, FR-22, FR-37, FR-48, and every FR and TR the QA commits cite. The reviewer wrote
none of this branch. Read-only on code; this report is the only file written; nothing committed.

Inputs read, not redone: `qa-cqrs-layout.md`, `qa-cqrs-layout-final.md`, `qa-duplicates-cqrs-layout.md`,
`claude-config-evaluation.md`, `db-notion-audit-cqrs-layout.md`, `.claude/docs/requirements/DIRECTION.md`.

## Evidence

| Check | Commit | Result |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | 718b4a3 | pass: 631 files formatted, ruff clean, mypy 625 files, 16 contracts kept |
| `make spec-check` | 718b4a3 | 0 problems; FR 51 current, 38 with a test; 114 commits read |
| `make py-test` | 718b4a3 | 7142 passed in 145 s |
| `make contracts-check` | 718b4a3 | pass, matches `afff924` of `contracts.lock` |
| `make chart-check` | 718b4a3 | pass: helm lint, 23 chart tests |
| `make py-test-integration` | 718b4a3 | 559 passed (5.3 GB available, no other Testcontainers suite) |
| `make py-test-messaging` | 718b4a3 | 34 passed |
| `test_guard.py`, `test_hooks.py` | 718b4a3 | 280 of 280, 193 of 193 (committed files, run from a `git archive` export) |
| Commit IDs | python..718b4a3 | 76 distinct FR and TR IDs cited, all in `spec.yaml`; TR-50 (removed) only named as removed |
| DB audit | c5f9392 | current: no later commit touches a migration or `schema.py`; 5 DIFFERENT (extra indexes), owner |
| QA sweep | 48e56cf, then cleanup to 718b4a3 | triaged; N1 to N20 each cited by a fix commit; L1 to L12 deferred |
| DIRECTION.md | ffbc220 | current enough (later commits change no behaviour or spec); verdict At risk, Must Drift |
| Kind (`proto-test-kind`, `proto-e2e`) | none | not run on this branch; decision `0025` section 6 owes it before the merge |
| Notion pages | not fetched | the DB audit fetched them at c5f9392; this review changes nothing there |

The QA cleanup report said integration and messaging were not run after the merges (memory). They are now run on
the head and pass, so that gap is closed.

The working tree is not clean: `.claude/hooks/guard.py`, `test_guard.py` and `test_hooks.py` hold about 500
uncommitted lines, the pending guard port from project-service (owner-run). Not reviewed here. A merge takes only
commits, so it does not carry them; the gates above ran on committed code (the hooks are not part of them).

## Constitution

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass, two gaps | Every code commit after the commit ID hook cites a real ID. `52ddc97`, `94d57e1`, `8e6c92b` touch code files (comments and doc paths only) with no ID; they predate the hook (`e00469d`). FRD and TRD have no "Constitution check" section (I.5); finding M3 |
| II Tech stack | pass, one gap | No runtime dependency added; psycopg pool extra dropped. pylint 4.1.2 and vulture 2.16 are dev tools approved in decision `0026` and listed in `TECH_STACK.md`, but II.1's tool list does not name them (II.3); finding M4 |
| III Architecture | pass | Layout as `0025`: domain kinds, `commands/`, `queries/`, `background/`, `ports.py`; 17 intent modules with one intent, one handler, one `handle` each; 4 contracts added, none removed or loosened. No migration. Rollback `ADMIN` only matches III.8 and FR-50 |
| IV Coding | pass | ruff, `mypy --strict` clean; one `type: ignore` removed (`700cd09`) |
| V Testing | pass | All suites above pass on the head; Kind not run (see Owner rulings) |
| VI Safety | pass | No added line runs kubectl, helm, doctl or reads `~/.kube`; `kind_safety.py` keeps the local Kind check before every kubectl call; no secret value in the diff; guard asks on settings, hooks and constitution edits |
| VII Docs | pass, small gaps | No em or en dashes in changed docs; tables exempt from the 120 rule; 3 frontmatter lines over 120 in `python-anti-patterns/SKILL.md`; findings I1, M1, M2 |
| VIII Agents | pass | Reviews written by separate agents; no push or merge in history |

## Spec coverage

| ID | Where built | Test | Status |
| --- | --- | --- | --- |
| TR-54 buses | `core/application/shared/cqrs.py`, `hosts/shared/buses.py` | `shared/test_cqrs.py`, wiring tests, contract `cqrs-is-a-leaf` | done |
| TR-55 feature folders | `core/application/<feature>/{commands,queries,background}`, `ports.py` | import-linter contracts, `test_kinds.py` | done |
| TR-2 domain kinds | `core/domain/{entities,values,enums,rules}` | contract `domain-kinds`, `test_kinds.py` | done |
| FR-50, TR-48 rollback `ADMIN` only | `shared/action.py`, `operations/gate.py`, `config/identity.py` | `test_gate.py`, `config/test_identity.py` | done in code; Drift in OpenAPI and MCP text (I1) |
| FR-19, FR-21, FR-22, FR-48 3-try go-live | not built | FR-21.1 and FR-22.1 tests prove 1 try | Drift, FRD marks "not built yet" |
| FR-1, FR-2, FR-5, FR-8, FR-43 | unchanged by this branch | see DIRECTION.md | Drift, known and planned |
| Reconciler, site catalog login, owner `deploy_release` and `promote_release` | `reconcile/background/`, `hosts/bootstrap/tenants.py`, `operations/commands/` | existing tests | Orphan (DIRECTION Q-4) |

QA merges spot-checked against their old copies, all equal in behaviour: `1302bec` (`_route_restored`, same moves,
same result per kind), `c5f9392` (`check_endpoint`, same checks and messages in the same order), `76db762`
(`split_url`, same parse and port refusal; DSN user and password read once), `a1fda02` (`read_entitlement_mode`,
same arguments), `409df81` (`publish_encoded`, same eight arguments), `17fd342` (`DELETE_ERRORS`, same six types
in the same order), `e75820a` (`_operator_audit`, same event fields), `183388a` (`kind_safety.py`, the same check,
and `_stack.py` still poisons `KUBECONFIG` before the renderer import).

## Findings

Critical: none.

Important:

- **I1. The published contracts still say an owner may roll back** (FR-50). Code since `082c77b` refuses any
  caller without `ADMIN` with 403 `ACCESS_DENIED` before the project is read (FRD FR-50.2). The texts say the
  opposite: `src/contracts/openapi.yaml:125-129` ("Every other caller needs project access as for any
  operation"), `src/contracts/mcp/tools.json:98` (same sentence) and `.claude/docs/guides/bruno/rollback_release.bru:30`
  ("Admin only in production"; it is every profile). A client or agent reading them expects the owner path.
  Fix: state "Only a caller whose roles hold `ADMIN` may call it, on any project, without the project access
  check; anyone else is 403 ACCESS_DENIED before the project is looked at", as `redeploy_release` says at
  `openapi.yaml:478-487`; add `tags: [admin]` like the other four admin operations; fix the Bruno line.

Minor:

- **M1. Decision 0025 CQ10 names the wrong error.** `.claude/docs/decisions/0025-domain-kinds-and-cqrs.md:51`
  says `send` of an unregistered type is `OperationNotAvailable`; the code raises `NotRegistered`
  (`core/application/shared/cqrs.py:48`, `:78`), and the hosts' start-up check makes it unreachable. Fix the record.
- **M2. `ai/README.md` does not list** `qa-cqrs-layout-final.md`, `qa-duplicates-cqrs-layout.md`,
  `db-notion-audit-cqrs-layout.md` or this report. Fix: add them under "Reviews still in use".
- **M3. FRD and TRD have no "Constitution check" section** (I.5). The FRD changed on this branch (`b4f424f`,
  `b5a3f50`, `ffbc220`) with no such section. Fix: spec-writer adds one to the FRD and designer to the TRD.
- **M4. Constitution II.1 does not list pylint and vulture.** They are in `TECH_STACK.md:56` and decision `0026`,
  approved by the owner. Fix: owner amends II.1 (Version 2) to add them as QA tools, or states in II.1 that dev
  QA tools live in `TECH_STACK.md`.
- **M5. The real-cluster rule differs between files.** `CLAUDE.md` section 0 says DOKS is off limits;
  `.claude/hooks/guard.py:38-42` asks for DOKS reads "until the owner picks one" (also a provenance note, VII.1).
  The pending guard port from project-service is the place to settle it; not reviewed here.
- **M6. Three commits change code files with no ID** (`52ddc97`, `94d57e1`, `8e6c92b`, comments and doc paths
  only, before the commit ID hook). History stays as it is; listed so the owner sees it.
- **M7. `.claude/skills/python-anti-patterns/SKILL.md:3`, `:8`, `:18`** run past 120 characters.

## Fix wave

1. FR-50 contract text (I1): `openapi.yaml` rollback description and tag, `tools.json` rollback description,
   `rollback_release.bru` line 30. Gates: `make py-test` (the contract tests read both files), `make spec-check`.
2. Docs (M1, M2, M7): decision `0025` CQ10 line, `ai/README.md` index, wrap the skill frontmatter. Small lane.
3. Spec (M3): a "Constitution check" section in the FRD and TRD, by the spec-writer and designer. Can follow the
   merge if the owner prefers.

## Deferred minors

No ledger with "minor (deferred)" lines was given. The QA "later" groups are triaged:

| Item | Decision | Reason |
| --- | --- | --- |
| L1 stale `bin/kind-state/.../context/src/` copy in PyCharm | defer, owner IDE setting (QA Q1) | not repo code |
| L2 SQL dialect, L3 transitive packages | defer, owner IDE setting | IDE configuration |
| L4 to L8, L10 inspections against intended code | drop | `mypy --strict` passes; each has a stated reason |
| L9 `_gen` name | drop | decision `0022` E3 |
| L11 one function at complexity 16 | defer | under the gate of 18; watch |
| L12 kept duplicates (commands, buses, two views) | drop | one intent, one module (`0025`); `0023` G9 |
| Duplicates "left with reason" in the cleanup report | drop | different reasons to change, as each states |
| Test shadowed names in Kind-only suites | defer to the next Kind task | cannot be run here |
| DB audit: 5 extra indexes | owner ruling | constitution III.3; not changed by this branch |
| M4, M5 | owner | constitution and guard settings ask the owner |

## Owner rulings before the merge

1. **Kind.** Decision `0025` section 6 owes one Kind run (`proto-test-kind`, `proto-e2e`) before the merge,
   because the hosts' wiring changed; `a9b7126` also changed the Kind rollback token. Give a slot, or rule that
   it follows the merge.
2. **DIRECTION.md Q-1 to Q-4.** FR-2 and FR-8 now or later; the 3-try go-live rule (FR-19, FR-21, FR-22, FR-48)
   before or after the merge; TR-26 skew and RS256 gaps into "Drift seen"; FRs for the three Orphans. Must Drift
   stays open until you rule; this review accepts it only as listed carry-overs.
3. **DB audit.** The 5 extra indexes (keep with a decision, or ask the Notion page to show them).
4. **Constitution II.1** (M4) and the **DOKS read rule** in the guard port (M5).

## Not proven

- Kind: admission policies, RBAC, the preview host, the Kind rollback with the admin token (never run here).
- DOKS, TLS, the Gitea pull Secret, backups and scale: a local run cannot show them.
- Notion was not fetched again for this review; the DB audit's fetch at `c5f9392` is the latest.
- Coverage (`make py-cover`) not rerun; decision `0025` reports domain 99.1% and application 96.9%.
