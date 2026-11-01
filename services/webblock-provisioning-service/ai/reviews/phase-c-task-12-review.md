# Phase C Task 12 review: sub-domain hostnames (G18) and migration 00023 (G18a)

- Stage: 5 Verify. Phase C exception (`.claude/rules/spec-driven.md`): cites decision 0023 G18, G18a and G20, no FR IDs.
- Reviewer: provisioning-validator, 27 October 2026. Read-only; nothing was edited or committed.
- Range: `git diff 597bcfa~1..59a2c31` on branch `phase-c` (72 files). The branch head (`2be6153`) has no change under `src`, `tests`, `deploy`, `scripts`, `mk` or the lock after `59a2c31`, so the gates below ran on the reviewed code.
- Sources: handoff `ai/handoffs/phase-c.md` (next step 1, open for the owner), ledger `task-12-brief.md`, `task-12-report.md`, `progress.md`, decision 0023 lines 78 to 80, Notion "5 · Provisioning" and "2 · Project" (fetched read-only today), Notion search over platform-db-v6 for the preview host and the bootstrap grant.

## Verdict

**Needs fixes before Task 12 closes**, for one reason only: the owner's preview-hostname decision (I1 and I2 below). The code itself does what the brief, G18 and G18a ask, the gates pass, and the database still matches Notion. No code fix is required from this review unless the owner picks a different preview form.

## Requirement table

| ID | What it asks | Evidence | Status |
| --- | --- | --- | --- |
| G18 | Site host `<subdomain>.<site domain>` | `routing.py:160-162` `site_hostname(subdomain, ...)`; callers in `routing.py` (point, reclaim, switch, restore, operator rollback), `hosting.py:334-339`, `recovery.py:150-155`, `reconcile.py:373-380`. Tests: `test_routing.py::test_site_hostname_is_the_subdomain_under_the_site_domain`, routing fakes assert the sub-domain (`test_routing_support.py`), `test_reconcile.py` fake asserts the record's sub-domain host, `test_render.py::test_the_site_route_names_the_subdomain_host` | done |
| G18 | Preview host with its existing prefix | `routing.py:166-168` `preview-<subdomain>.<site domain>`; `test_preview_hostname_is_preview_dash_the_subdomain_under_the_site_domain` | done, but see I1 and I2 (owner) |
| G18 | Admission policy checks the host | `site-route-shape.yaml:54-66, 92-97` reads label `websites.webblock.internal/subdomain`; no label means no attached route. Unit: `tests/unit/deploy/test_route_shape_policy.py` (text checks). Kind: `test_dry_run_live.py` (other sub-domain, project-ID host, preview on site host), `test_site_policies_live.py` | done; Kind tests written, **not run** |
| G18 | Renderer refuses other hosts (takeover T1) | `render.py:403-416, 470-471`; `test_render.py::test_a_route_names_no_other_projects_platform_hostname` (other sub-domain, own plus other, under own, the site domain, the old project-ID host) and `test_a_subdomain_that_is_not_one_dns_label_is_refused` | done |
| G18 | e2e harness on the sub-domain | `tests/e2e/_stack.py:362-364, 588-590, 758-760` | done; e2e not run (Kind) |
| G18a | Grant `SELECT (subdomain)` to `provisioning_bootstrap` in 00023 | `src/migrations/00023_bootstrap_reads_subdomain.sql:48`, migrator and missing-role guards, no Down, no default | done |
| G18a | Migration rules, upgrade test | `tests/integration/test_schema_00023.py`: read only after 00023; every other hosting column, `SELECT *`, update, insert, delete, truncate still refused; exactly one privilege changes across all roles and `public`; rows kept from 00022; refuses another role (42501); refuses a missing role; no Down/default/CASCADE/REVOKE/ALTER | done |
| G18a | Bootstrap writes the sub-domain into the tenant | `tenant_projects.py:47-63` reads `(project_id, subdomain)` with a one-column EXISTS (no `SELECT *` on releases); `loop.py:134-148, 168-172`; `bootstrap.py:96-102` label on create and added once; `bootstrap.py:147-152` keeps it on the delivered mark; `bootstrap-scope.yaml:85-94, 133-139` label is one DNS label, set once, never changed or removed. Integration `test_tenant_projects.py::test_each_project_comes_with_its_own_subdomain` (as the bootstrap login). Kind `test_bootstrap_scope_live.py` (not a label, changed, removed refused; added once admitted) | done; Kind part not run |
| G18a | Change report | `/home/roa/Documents/web-block/platform-db-rev25-provisioning-00023-report.md` (changed, not changed, UI impact) | done |
| G20 | Database matches Notion | Notion "5 · Provisioning" lists `provisioning_bootstrap` as "reads only" and lists no per-column grants; 00023 adds a read only and changes no table, column, type, order, constraint, trigger or row. No difference found | done |

The claim in `tenant_projects.py` that every release has a hosting row is true: the guard `releases_insert_shape` (`00011_rename_for_clarity.sql:288-293`) refuses a release without one, and no role may delete hosting rows. So moving the read to start from `project_hosting` drops no project.

## Commands and results

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | passed | 537 files formatted, ruff clean, mypy strict no issues in 533 files, 9 import contracts kept |
| `make py-test` | passed 6958 | 353 s |
| `make py-test-integration` | passed 551 (253 deselected) | migration and SQL changed; no Kind stack up, about 5950 MB available at start |
| `make py-test-messaging` | not run | no broker, worker consumer or outbox change |
| `make contracts-check`, `make chart-check` | not run | not asked; no contract or chart change in the range |
| Kind suites (`proto-test-kind`, `proto-smoke`, `proto-e2e`) | not run | reviewers never run Kind; the policy and e2e tests of this task are proven only by kind-runner (handoff step 5) |

## Findings

### Critical

None.

### Important (owner decision; blocks closing Task 12, as the handoff says)

- **I1. Preview host too long for valid sub-domains.** `routing.py:166-168` builds `preview-<subdomain>.<site domain>`. Notion "2 · Project" allows a sub-domain of 3 to 63 characters, so `preview-` plus a sub-domain of 56 to 63 characters is not a DNS label. The renderer refuses it, so such a site can never be promoted (`route_refused` on every promote). Nothing in Provisioning can fix this without changing the preview form or the sub-domain rule.
- **I2. Preview host collides with a sub-domain that starts with `preview-`.** Project B with sub-domain `shop` has preview host `preview-shop.<d>`; project A may choose sub-domain `preview-shop` (Notion reserves names, not prefixes), and its site host is the same name. Both are admitted (each namespace's label matches). The worker probe always sends the preview token (`site_cluster.py:611-616`), and the Gateway prefers B's preview rule (it has a header match), so A's switch probe reaches B's candidate and A's promotion fails with `site_not_answering` for as long as B's preview route exists. Visitors are not affected (they have no token), so this is a reliability bug between tenants, not a data leak.

### Minor

- **M1. Running sites keep the old host.** `reconcile.py:365-372` repairs a route only when its target differs, so a route written before this change keeps `<projectId>.<site domain>` until the next promote, suspend, resume or rollback. Harmless for the prototype (the stack is recreated). Record it in 0023 (already carried to Task 9 in the ledger); a real upgrade would need the reconciler to compare hostnames.
- **M2. Policy proof is text, the behaviour is only in Kind tests.** `tests/unit/deploy/test_route_shape_policy.py` and `test_bootstrap_scope_policy.py:197-207` check the YAML text, not admission. The refusals are proven only by the `kind` tests, which have not run. Also no live test covers "a namespace without the sub-domain label admits no attached route" (`site-route-shape.yaml:92-94`); suggest one more case in `test_dry_run_live.py` when the Kind run happens.
- **M3. The renderer does not check the preview host.** `render.py:505-527` (`render_preview_route`) accepts any valid host; only admission refuses a wrong one. This was the same before Task 12. Defence in depth would pass the sub-domain and check `preview-<subdomain>.<site domain>` like `render_route` does.
- **M4. Stale namespace label list.** `docs/runbooks/prototype-stack.md:119` lists the tenant namespace labels without `websites.webblock.internal/subdomain`. The other stale texts (ARCHITECTURE lines 21, 204, 281, 317, 344, CLAUDE.md, AGENTS.md, 0023 line 123) are already carried to Task 9 and are not repeated here.
- **M5. No head-version column-order test.** `tests/integration/test_schema_00022.py:360-363` is now pinned to version 22. 00023 changes no column, so nothing is wrong today, but nothing asserts Notion's column order at the latest migration. The final DB audit (handoff step 3) covers it; a head-version test would keep it covered for later migrations.

## Security notes (for the lead; security-reviewer should run, since a grant and two admission policies changed)

- The hostname-takeover guard now depends on two things it did not depend on before: Project keeping sub-domains unique (Notion "2 · Project": unique over all projects, deleted ones included; Provisioning has no unique index and, under G20, may not add one Notion does not name), and the bootstrap identity writing the right label. A stolen bootstrap token can create a namespace with any sub-domain label, but it still cannot write a route (only the worker can, `tenant-writer.yaml`), so a takeover needs both identities or a worker bug plus a bootstrap bug. Before Task 12 the project-ID label was checked against the namespace name, so the cluster could verify it; the sub-domain cannot be verified in the cluster.
- The bootstrap still reads no other column and writes nothing in the database (proven by `test_schema_00023.py`). No secret values in the diff; no em or en dashes in added lines.

## Owner question: the preview hostname (recommendation)

Notion does not define the preview host. The options:

1. **Recommended: `<subdomain>.preview.<site domain>`.** A sub-domain is one DNS label, so this can never collide with any site host and always fits (label of at most 63). It needs no change to Project or to Notion. Cost, all inside Provisioning: `preview_hostname` and the route-shape policy's preview host, the wildcard Certificate gains the name `*.preview.<site domain>` (the Gateway listeners `*.<site domain>` already match it by suffix; the probe connects to the Gateway address with the host as SNI, so no public DNS is needed), golden manifests, the preview-only sample site comment and a Kind rerun. It changes the wording of 0023 G18 ("with its existing prefix"), so it needs a recorded decision.
2. Project reserves the prefix `preview-` and limits sub-domains to 55 characters. Smaller change here, but it changes Notion "2 · Project" (3 to 63 characters, names reserved, not prefixes), needs the Project team, and Provisioning cannot enforce it, only fail the promotion.
3. Keep it as is and accept I1 and I2 as known limits. Not recommended: valid sub-domains under Notion's own rules would fail to go live.

## Skipped checks and blockers

- Kind suites and e2e: not run by design; Task 12 is not fully proven until kind-runner runs them (handoff step 5).
- Notion pages platform-db-v6 and Database Design were edited on 26 October, after Task 12; the final DB audit (handoff step 3) should fetch them fresh.
