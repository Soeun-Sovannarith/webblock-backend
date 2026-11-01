# Handoff: Provisioning after the cqrs-layout merge

Read this first. Then `.claude/README.md` (hooks, rules, skills, agents), `.claude/WORKFLOW.md` and
`.claude/docs/requirements/README.md`. The ledger (git-ignored, every ruling with its cost if wrong) is
`.superpowers/sdd/2026-10-25-provisioning-phase-c/progress.md`.

## Where things stand

- `python` is at `39add4d`. Phase C (merge `2c4a5ea`, decisions in `0023`) and `cqrs-layout` (decision `0025`: domain
  kinds, CQRS buses, `<feature>/{commands,queries,background}`; TR-2, TR-54) are both merged. Nothing is pushed.
- Last gates, on `718b4a3`: unit and contract 7142, integration 559, messaging 34, chart 23, contracts match,
  spec-check 0 problems (`ai/reviews/cqrs-layout-final-review.md`). Kind has never run on Phase C or cqrs-layout code.
- Rollback is `ADMIN` only in code and in the contract texts (FR-50, TR-48). The go-live retry rule is in the spec
  (FRD FR-19, FR-21.1 to FR-21.6, FR-22.1, FR-22.2, FR-37.3); FR-48 stays with the automatic flow. No code yet.
- DB audit `ai/reviews/db-notion-audit-cqrs-layout.md`: 5 DIFFERENT, all extra indexes. The next migration file is
  `00026`. Migration 00024 was reverted; if it ever ran on a dev or test database, drop its index and goose version
  24 there by hand.

## Owed, in this order

1. The 3-try go-live rule, on branch `go-live-tries` (worktree `.claude/worktrees/go-live-tries`): design, plan,
   tests first, then code. Try counter `command_dispatch.execution_attempts`; waits of 30 s and 60 s through
   `retry_after`; no database change; no release state move Notion does not allow (a `candidate_ready` release moves
   to `promoting` first). A preview that fails on a fresh start is a failed try.
2. Migration `00025` drops the 5 extra indexes (constitution III.3): written, reviewed, audited with zero
   differences (branch `db-00025`, merged into `release`). The owner runs `make migrate-up` after the merge.
3. Constitution check section (I.5) in the FRD and TRD (finding M3 of the final review); spec-writer and designer.
4. Admin end-to-end tests and client docs (new work, its own plan with FR IDs):
   - e2e, marker `e2e`: admin suspend then resume of a live site (FR-33, FR-34); admin suspend while `billing` holds
     the site offline: after the admin resume it stays offline and the resume ends `skipped`
     `hosting_already_online` (FR-34); redeploy of a live site keeps the release `live` with the same digest
     (FR-35); a `USER` token on each admin path is 403 (FR-4); the admin read shows `requestedBySubject` and the
     client read does not (FR-36, FR-39); an `ADMIN` rollback of another owner's uncertain site (FR-50, FR-51).
   - Client docs (FR-37 to FR-41, FR-46): the step order in `openapi.yaml`, the MCP `outputSchema` follow-up, the
     metrics `route` label note, the admin retry rule in `0023`, a separate `adminToken` in Bruno.
   - Small carry-overs from the ledger (search "carry"): the `0021` column-order note, `ANALYZE` after 00022.
5. Carried to the next final review: R1 (the Kind case `preview-route-with-the-header-removal-twice`: the CRD
   refuses it before the policy), M1, M2, M3, M5 of the Task 12 review, `site_cluster.apply_release` keeping an
   existing `restart-command` value when not restarting, the ledger's deferred minors.
6. Kind run (`kind-runner`, `kind-slot-run` skill; waits on an owner slot of about 7000 MB free):
   `make proto-down; make proto-up; make proto-test-kind; make proto-run; make proto-e2e` on the final code, results
   into `0023` and `0025` section 5. It must cover cases written but never run:
   - the preview host `<subdomain>-preview.<site domain>` (G18b, FR-20): the one wildcard certificate
     `*.sites.webblock.test` covers it (no other name; the TLS check of `<subdomain>.preview.sites.webblock.test`
     fails), the probe over HTTPS with that server name, the `preview-only` sample site (image rebuilt for its new
     regex), the refusal of the dotted `<subdomain>.preview.<site domain>` and the old `preview-<subdomain>` hosts
     (`test_dry_run_live.py`, `test_site_policies_live.py`); preview answers carry `Cache-Control: private,
     no-store` (`test_kubernetes_live.py`) and the policy refuses a preview route without it; the bootstrap policy
     refuses a new sub-domain label ending in `-preview` or of 56 characters (`test_bootstrap_scope_live.py`);
   - `tests/integration/kubernetes/test_kubernetes_live.py`: the preview answers only the project's own token
     (G18c); the raw secret and another project's token get 404; `test_probe_live.py`: `readyz_headers` sends it;
   - `tests/integration/stack/test_site_policies_live.py`: renderer output admitted with the header removal; the
     refused preview and site route cases of rules S1, S3, S4 and L1;
   - `tests/integration/policy/test_catalog_secret_mark_live.py` `test_s6_*`: only the bootstrap changes the
     sub-domain label (S6);
   - `tests/integration/kubernetes/test_dry_run_live.py`: the preview with the new keywords;
   - the hosts' wiring after the cqrs-layout move (`0025` section 6) and the Kind rollback with the admin token
     (`a9b7126`). In `tests/e2e/`: `test_scenarios_mcp.py::test_every_tool_is_called_end_to_end` and
     `::test_a_token_without_the_scope_is_refused_by_every_tool_and_route`;
     `test_scenarios_promote.py::test_an_unconfirmed_restore_leaves_the_route_uncertain_until_the_operator_rolls_back`;
     both tests of `faults/test_hosting_retry.py`; both `test_qa07_*` tests of
     `faults/test_recovery_route_reclaim.py` (through `Site.command`).
7. The auth and telemetry alignment: `ai/handoffs/auth-telemetry-alignment.md` (decision record 0024 first).
   web-block-commons is merged to `main` in its own repo; FR-2 and FR-8 come with its adoption.
8. Chart fixes before any DOKS deploy (found by project-service-14's read-only check; a planned task with IDs, when
   the owner sets a deploy date):
   - pods carry `app.kubernetes.io/part-of: webblock-provisioning` (`templates/_helpers.tpl:6`), but
     web-block-config lets only `part-of: provisioning` reach the database;
   - the broker egress opens 5672 (`templates/networkpolicies.yaml:63`, `:104`); DOKS RabbitMQ is AMQPS on 5671
     only, and the chart has no broker CA setting (TRD "Open infrastructure notes");
   - the API has no egress to the Keycloak key set or to Project on 9090 (`networkpolicies.yaml:35-37`);
   - every egress rule is a `toCIDR` /32 (`_helpers.tpl` `provisioning.cidrEgress`), which Cilium does not match for
     in-cluster pods or Services; in-cluster targets need endpoint or service selectors.
9. `DIRECTION.md` lines 265 to 266 say Project's gRPC `CheckAccess` is not on Project's `main`; it is now (Project
   `main` `ed5fcaf`). The next direction check corrects it, and FR-5 task 11 can be planned.

## Rules in force

- Spec-driven (`.claude/rules/spec-driven.md`): no FR ID, no code; tests name their FR ID. The constitution:
  `.claude/docs/CONSTITUTION.md`.
- One implementer at a time; a different agent reviews. Commit named files only. Never push (the owner pushes).
- The Notion DB design never changes, and the database matches it 100%: no extra index, column, grant or failure
  code. The "Web Block architecture" page may change; only the lead edits it.
- FR-54: a sub-domain another project holds is caught in code (advisory lock, then a check where the login may read
  `subdomain`; the release-intake login cannot, so the bootstrap refuses the tenant). Residual: a writer outside
  Provisioning; Project's database keeps sub-domains unique. FR-54.5 is reachable only from a store that can read
  `subdomain`; the owner accepted no new grants.
- FR-53 and TR-52: private site images; the bootstrap writes a pull Secret into each `wb-<projectId>`.
- Worktrees: copy the pinned tools into the worktree `bin/` (uv, protoc, goose, osv-scanner, grype, helm), symlink
  `bin/uv-cache` to the shared one, run `make py-sync`. Run `make py-test` with
  `CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts`; the default `../web-block-contracts` path is
  wrong inside `.claude/worktrees/`.

## Open for the owner

- A Kind slot (item 6).
- Constitution II.1 amendment for pylint and vulture.
- FR-2 and FR-8 now or with the web-block-commons adoption; `DIRECTION.md` Q-1 to Q-4 (the 3-try rule is planned
  after the merge; TR-26 skew and RS256 gaps into "Drift seen"; FRs for the three Orphans). Must Drift stays open
  until you rule.
- DOKS read rule in the guard: Project's rule is not ready (its security review found holes; a second fix round
  runs). `DOKS_READ = "ask"` stays in `.claude/hooks/guard.py` until Project's lead sends the final commits and the
  passing review. The port is done in a worktree with tests; the owner copies the hook edits in.
- Cross-repo (contract matters go to the owner through project-service-14, which owns contracts, commons and the
  platform diagram). Project accepted the `repository.proto` shape: an unknown project answers `NOT_FOUND` with
  reason `REPOSITORY_NOT_FOUND`, the caller is `provisioning-build-worker`; the `clone_url` host is the owner's
  call. Project consumes `publish_completed.v1` (the first one makes the draft active; it de-duplicates on
  `event_key`); the Notion queue entry and the broker read right are the owner's.
- MVP site domain (`0016` section 10): sites run on k3s on your EC2 under `roaa15.xyz`, Cloudflare Tunnel on the EC2
  sends `*.roaa15.xyz` to Traefik, previews stay internal. Your part: add the zone to Cloudflare, create the tunnel
  and the wildcard route, and rule on OD-71 (FR-20 and FR-52 read as a public preview host). The k3s spike step 1
  (local k3d) runs after go-live tries Tasks 2 to 9; the decision 0025 end-to-end run then happens on k3d, not on a
  separate Kind run. Step 2 (EC2) is yours.
- Push `python`. `web-block-contracts` is pushed (`git@github.com:itme-roa15/webblock-contract.git`, `main` at
  `18dd301`) and goes to the Payment team. Provisioning pins `afff924`; the newer commit adds Project's answers,
  the action list, the Payment admission drafts and the needs table, and leaves Provisioning's files unchanged.
  Move the pin (`contracts-pin` skill) when a task needs those files.
- Tell Project: add the label `preview` to its reserved sub-domains (`webblock.platform.reserved-subdomains` holds
  www, app, api, admin, git, mail, status, docs, auth; no `preview-` prefix rule is asked, because our preview host
  is `<subdomain>.preview.<site domain>`). It is an FR-13 change on Project's side, still open. Also decide on
  `xn--` names, and for production list the site domain on the Public Suffix List. Project keeps sub-domains unique
  (`projects_subdomain_key`; its label rule is a subset of `project_hosting_subdomain_check`).
- Tell the frontend about the breaking REST change (contract 0.2.0); Project and Payment to accept the draft protos
  and events.
- Re-sign or upgrade the two `certifi` vulnerability exceptions before they expire (`security-exceptions` skill).
- Notion: platform-db-v6 "Which service talks to which" says HTTP between services; "Web Block architecture" says
  gRPC. The code follows the architecture page and keeps the HTTP access client as a mode.
- Local `.env`: copy `.env.example`, split per process as its header shows, fill the `change-me` values.

## Infrastructure outside this repo (web-block-config, owner)

See `.claude/docs/requirements/TRD.md` "Open infrastructure notes": registry path `/v2/` reachable by the nodes
(Cloudflare Access bypass, Gitea still asks for the token); Cloudflare upload size for Jenkins pushes; the Gitea
organization `webblock`; Cloudflare Access headers from Provisioning to Jenkins and the chart NetworkPolicy (Jenkins
only on 8080); the broker's own CA.
