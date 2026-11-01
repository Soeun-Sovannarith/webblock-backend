# Review: preview-host (one-label preview host)

Stage: 5 Verify, small change lane (closes the recorded FR-20, FR-52 and FR-55 Drift).
IDs: FR-20 (FR-20.1), FR-52 (FR-52.1, FR-52.2), FR-55 (FR-55.1 to FR-55.4); TR-35, TR-45.
Range: `git diff 17c8b2c..8877933` on branch `preview-host` (commits 16f6aa7, 1013ed9, 8877933).
Checked against: `.claude/docs/changes/preview-host.md`, FRD rows FR-20, FR-52, FR-55, TRD TR-35 and TR-45, decision
0023 G18b and G18c, Notion "5 · Provisioning" (read-only fetch).
Lead rulings accepted as given: no extra policy guard for namespaces labelled before this change; the 181 character
site domain bound stays.

## Verdict

Approved, with one Medium and three Low findings that the lead may fold into this branch or carry. No High finding.
The code does what the FR rows say, the shared rule has one copy, there is no database change, and every gate I could
run passed. The live proof (admission policy, certificate, preview GET with and without the token) is Kind only and
was not run: it belongs to kind-runner. Security-reviewer must run too: the change touches an admission policy, the
Gateway certificate and where the probe sends the preview token.

## Constitution

The brief cites I.6; Version 2 of the constitution has no I.6 (Article I ends at I.5). I checked I.1 to I.5.

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | FR-20, FR-52, FR-55 rows (PREVIEW1) were in the FRD at 17c8b2c with the Drift recorded; the code follows them and 8877933 removes only the Drift note. Every commit cites its FR IDs. Small change lane: a change note, no design file needed. |
| II Tech stack | not applicable | No dependency, image or tool change. |
| III Architecture limits | pass | III.1: the rule lives in `core/domain/values/text.py` beside `validate_subdomain`. III.2: `text.py` imports only `core.domain.errors`; application and infrastructure import it from `provisioning.core.domain`; `make py-layers` 16 kept, 0 broken. III.3: no migration or `schema.py` change; Notion shows `project_hosting.subdomain VARCHAR(63) NOT NULL`, unchanged; FR-55 is code only. III.5: the 55 character cap on Project's side is "asked through project-service-14", not assumed. III.6: only the bootstrap loop refuses; only the worker renders routes. III.7: the token is still removed before the site; the probe sends it only to a `-preview` label under the site domain. |
| IV Coding conventions | pass | `mypy --strict` and ruff clean; no new `noqa` or `type: ignore` in `src/`. |
| V Testing and quality | pass with finding M1 | New tests name FR-20, FR-52, FR-55 rows; negative cases present (old dotted host, old `preview-` host, 56 to 63 characters, deeper names, other domains). FR-52.1 and FR-52.2 have no test naming the row (M1). |
| VI Safety guardrails | pass | No secret value in the diff; refusal log carries the project ID and a reason code only. No push, no cluster call in this review. |
| VII Docs | pass with findings L1, L2 | No em or en dash in any changed file. Docs touched state the new host as current fact. Two docs outside the diff still state the old host (L1). Long lines only in table rows and spec.yaml node IDs, as in those files before (L3). |
| VIII Agents | pass | I wrote none of this change; this report is my only write. |

## Requirement table

| ID | Tests (node names) | Status | Evidence |
| --- | --- | --- | --- |
| FR-20.1 | `test_routing.py::test_fr20_1_the_preview_hostname_is_the_subdomain_dash_preview_under_the_site_domain`, `test_preview_label.py::test_fr20_1_the_preview_label_is_the_subdomain_and_dash_preview`, `test_fr20_*` (55 characters, older hosts never built), `test_render.py::test_fr20_s3_a_preview_route_names_only_its_projects_preview_host`, `test_route_shape_policy.py::test_fr20_*`; Kind: `test_dry_run_live.py` case `fr20-preview-on-the-old-dotted-preview-host`, `test_site_policies_live.py` case `fr20-preview-route-on-the-old-dotted-preview-hostname` | done in unit; live GET not run | `routing.py:185-190` builds `<preview_label>.<site domain>`; `render.py:530-534` refuses any other host; policy CEL `site-route-shape.yaml:73-76`. The GET of the row is proven only by `test_kubernetes_live.py::test_both_routes_name_their_backend_and_the_preview_is_fenced` (uses `preview_hostname`), Kind, not run, and not named FR-20.1 (M1). |
| FR-52.1 | none names it | missing ID link (M1) | Behaviour exists: `test_render.py::test_s1_two_projects_preview_routes_match_different_values`, Kind `test_kubernetes_live.py:324-330` (no token, raw secret, other project's token give 404). The one linked test, `test_site_cluster.py::test_fr52_the_probe_sends_the_token_only_to_a_dash_preview_host`, proves where the probe sends the token (G18c), not this row. |
| FR-52.2 | none names it | missing ID link (M1) | Behaviour exists: the preview rule's `requestHeaderModifier remove: [X-Webblock-Preview]` asserted in `test_render.py:511` (`test_the_preview_route_is_fenced_on_the_gateway_with_its_one_hostname`); unchanged by this diff. |
| FR-55.1 | `test_preview_subdomain.py::test_fr55_1_preview_alone_or_not_at_the_end_gets_its_tenant`, `test_preview_label.py::test_fr55_1_these_subdomains_are_hosted`, `test_routing.py::test_fr55_a_preview_host_is_never_the_site_host_of_a_hosted_subdomain` | done | `preview`, `preview-shop`, `shop-previews` get tenants; no error line. |
| FR-55.2 | `test_preview_subdomain.py::test_fr55_2_a_dash_preview_subdomain_gets_no_tenant_and_one_error_without_it[shop-there,shop-absent]`, `test_preview_label.py::test_fr55_2_*`, `test_routing.py::test_fr55_the_one_collision_is_a_subdomain_the_bootstrap_refuses` | done | `bootstrap_loop.py:184-192`: after the FR-54 check, same shape: `return False` before `ensure_tenant`, one `error` line with `project_id` and `reason`, no sub-domain; test asserts the stand-in rows unchanged and the other projects ready. |
| FR-55.3 | `test_preview_subdomain.py::test_fr55_3_a_refused_subdomain_is_never_remembered_as_ready` | done | Refusal returns before `self._ready[...]` (`bootstrap_loop.py:205`); two rounds, two error lines, `is_known_ready` false. |
| FR-55.4 | `test_preview_subdomain.py::test_fr55_4_a_subdomain_of_56_to_63_characters_is_refused_and_55_is_not[56,60,63]`, `test_preview_label.py::test_fr55_4_*[56..63]`, `test_render.py::test_fr55_4_a_subdomain_over_55_characters_has_no_preview_route` | done | `text.py:163`: over 55 gives `subdomain_too_long`; 55 gets its tenant; the renderer also refuses a 64 character label (defence in depth). |
| TR-35 | `test_route_shape_policy.py::test_fr20_the_preview_host_is_the_subdomain_dash_preview_and_the_old_forms_are_gone`; Kind cases above | done in unit (CEL text); live not run | `allowedHost` is `subdomain + '-preview.' + siteDomain`; every hostname must equal it, so the dotted and `preview-` hosts are refused. |
| TR-45 | `test_route_shape_policy.py::test_fr20_the_one_wildcard_certificate_covers_the_preview_host` | done in unit; live not run | `10-wildcard-certificate.yaml` names only `*.sites.webblock.test`; the test checks one-label coverage of the new host and none of the dotted host. |

Test first: tests and code land in the same commits, so history cannot show the failing run, and the reviewer hook
blocked extracting 17c8b2c to rerun the tests on the old code. By inspection every new assertion fails on 17c8b2c
(the names `preview_label`, `subdomain_refusal`, `MAX_HOSTED_SUBDOMAIN_LEN` do not exist there; `preview_hostname`
returned `shop.preview.<d>`; the old `_is_preview_host` sent the token to the dotted host; the bootstrap had no
refusal), except `test_fr55_1_preview_alone_or_not_at_the_end_gets_its_tenant`, an honest guard for an allowed case.
Reported as "by inspection, not run".

Shared rule: `preview_label`, `subdomain_refusal`, `PREVIEW_LABEL_SUFFIX` and `MAX_HOSTED_SUBDOMAIN_LEN` are defined
only in `src/provisioning/core/domain/values/text.py:148-165` and used from `routing.py:190`, `bootstrap_loop.py:184`,
`render.py:533`, `site_cluster.py:581-582`. The only other copies are the CEL policy and the nginx test fixture,
which cannot import Python. `text.py` imports nothing outside the domain.

## Commands and outcomes

`free -m` before the runs: 3177 MB available, no Testcontainers or Kind suite started.

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` (with `CONTRACTS_REPO`) | passed | 636 files formatted; ruff all checks passed; mypy no issues in 630 files; import-linter 16 kept, 0 broken |
| `make spec-check` | not run | The reviewer hook refuses it ("not a check target"); I did not work around it. Instead: all 20 node IDs listed under FR-20, FR-52, FR-55 in `spec.yaml` collect (`pytest --collect-only`, 50 items with parameters); FR-55 left `no_test_yet`. The lead or implementer should quote its counts. |
| `make py-test` | passed 7245 | 157 s, no warning, no skip |
| `make contracts-check` | passed | matches `afff9245444c628b74f88d281939f4bc8b4de3e2` |
| `make chart-check` | passed 23 | helm lint and `pytest -m chart` |
| `.venv/bin/python -I scripts/prototype/render-examples.py --check` | passed (exit 0) | `deploy/prototype/manifests/` match the renderer; `test_manifest_examples_drift.py` also in `py-test`; golden `route-preview.yaml` checked by `test_render.py` in `py-test` |
| Notion "5 · Provisioning" fetch (read-only) | done | `project_hosting.subdomain VARCHAR(63) NOT NULL`; no migration, `schema.py` or role in the diff |
| `make py-test-integration`, `py-test-messaging` | not run | No schema, store, SQL or broker change |
| Kind: `test_dry_run_live.py`, `test_site_policies_live.py`, `test_kubernetes_live.py`, `test_probe_live.py`, `proto-e2e` | not run | Kind belongs to kind-runner in an owner slot |
| `make py-vuln py-age image-scan` | not run | No dependency or image change |

## Findings

### High

None.

### Medium

- **M1. FR-52.1 and FR-52.2 have no test naming the row; FR-20.1's GET has no test naming it.** The change note
  promises `test_fr52_1_*`, but the only FR-52 test,
  `tests/unit/infrastructure/kubernetes/test_site_cluster.py:1179`, proves G18c (the probe sends the token only to a
  `-preview` host). The tests that do prove FR-52.1, FR-52.2 and the FR-20.1 GET carry no FR ID:
  `tests/integration/kubernetes/test_kubernetes_live.py:290` (404 with no token, the raw secret or another
  project's token; 200 with its own), `tests/unit/infrastructure/kubernetes/test_render.py` around line 471 and 511
  (the header removal) and `test_s1_two_projects_preview_routes_match_different_values`. V.1 asks each test to name
  its FR ID. Fix: add "FR-20.1, FR-52.1" to the docstring of
  `test_both_routes_name_their_backend_and_the_preview_is_fenced`,
  "FR-52.2" to the header removal test, "FR-52.1" to `test_s1_two_projects_preview_routes_match_different_values`,
  and list them under FR-20 and FR-52 in `.claude/docs/requirements/spec.yaml:465` and `:885`. No behaviour change.

### Low

- **L1. Two docs still state the old host as current.** `.claude/docs/requirements/DIRECTION.md:202` says "`*.preview`
  certificate never checked on Kind" (that name no longer exists), and `ai/handoffs/after-phase-c.md:42-44` tells the
  Kind run to cover "`<subdomain>.preview.<site domain>`" and "the `*.preview.sites.webblock.test` certificate name".
  kind-runner reads the handoff, so it would check the wrong host. Fix: DIRECTION row TR-45 to "one wildcard
  `*.<site domain>` covering `-preview` hosts never checked on Kind"; handoff item 6 to the new host, the one name
  certificate and the refusal of the dotted host. (VII.1)
- **L2. The change note's constitution line marks only III.3, III.6, III.7.**
  `.claude/docs/changes/preview-host.md:24-25`.
  I.5 binds change designs; a small lane note is not one, so this is advisory. Fix (optional): one line each for
  Articles I to VIII, as the FRD's check does.
- **L3. Long lines in changed table rows and spec.yaml node IDs** (TRD TR-35 and TR-45, OD-71, the runbook policy row,
  the sample site README). These files already hold many such lines at 17c8b2c (spec.yaml 125, TRD 54,
  open-decisions 63, prototype-stack 44), and a table row or node ID cannot wrap. No fix asked; noted for VII.2.

## Notes for the lead

- Security-reviewer: the route-shape policy CEL (`deploy/prototype/policy/site-route-shape.yaml:73-76`), the
  certificate (`deploy/prototype/gateway/10-wildcard-certificate.yaml:15-17`) and `_is_preview_host`
  (`src/provisioning/infrastructure/kubernetes/site_cluster.py:575-585`), which now treats any `<label>-preview` label
  directly under the site domain as a preview host.
- kind-runner: the new cases in `test_dry_run_live.py` and `test_site_policies_live.py`, the certificate reissue
  without `*.preview.sites.webblock.test`, and the `preview-only` sample site image, whose nginx regex changed
  (`deploy/prototype/sample-site/preview-only/default.conf:17`), so that image must be rebuilt before `proto-e2e`.
- Skipped checks and blockers: `make spec-check` (hook), the base rerun for test first (hook), every Kind suite.
