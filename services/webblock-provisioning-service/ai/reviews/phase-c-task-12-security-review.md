# Security review: Phase C Task 12 (sub-domain hostnames, migration 00023, admission policies) and its fix round

Stage 5 Verify, Phase C exception: cites decision 0023 G18, G18a, G18b instead of FR and TR IDs.
Date 27 October 2026. Branch `phase-c`, commits `597bcfa`, `59a2c31` and fix round `a790542` (branch head
`d97377f`; nothing under `src`, `deploy`, `tests` or `scripts` changed after `a790542`). Reviewer:
security-reviewer, Claude Opus 5.5. Read only; nothing edited or committed.

## Method

- Read: `.claude/agents/security-reviewer.md`, `ai/reviews/phase-c-task-12-review.md` (validator, "Security notes
  for the lead"), decision 0023 lines 78 to 89, `ai/handoffs/phase-c.md`, and at the branch head:
  `deploy/prototype/policy/site-route-shape.yaml`, `bootstrap-scope.yaml`, `tenant-writer.yaml`,
  `site-params.yaml.tmpl`, `deploy/prototype/gateway/10-wildcard-certificate.yaml`, `20-gateway.yaml`,
  `deploy/prototype/rbac/20-tenant-worker.yaml`, `30-tenant-bootstrap.yaml`, `10-identities.yaml`, the Helm
  service accounts (build worker), `src/migrations/00020`, `00021`, `00022` (subdomain parts) and `00023`,
  `routing.py`, `render.py`, `site_cluster.py` (routes, probe), `bootstrap.py`, `tenant_projects.py`,
  `presentation/messaging/v1.py` (where the sub-domain enters), `config/worker.py` (probe token, Gateway URL).
- Skills: sharp-edges and differential-review reasoning applied by hand (diff of `597bcfa~1..a790542`, code paths
  only; the doc deletions in that range were skipped).
- Ran: `uv run pytest tests/unit/deploy tests/unit/core/application/test_routing.py
  .../test_routing_support.py tests/unit/infrastructure/kubernetes/{test_render,test_bootstrap,test_site_cluster,
  test_site_probe}.py tests/unit/test_manifest_examples_drift.py`: 354 passed.
- Not checked: no cluster or Kind command (by brief); admission behaviour is reasoned from the CEL, the Kind
  policy tests are written but not run. `make py-vuln py-age` and `make image-scan` not run: no lock, pin or
  image change in the range. Cilium's tie-break between two routes with the same host was not verified live;
  the Gateway API rule (oldest route wins, then namespace/name) is assumed.

## Findings

### Medium

**S1. The preview token is sent to tenant code, so one tenant can open any other tenant's preview.**
`src/provisioning/infrastructure/kubernetes/site_cluster.py:615` (the probe always sets `X-Webblock-Preview`),
used for the preview host (`routing.py:650`) and for the site host after the switch (`routing.py:681`);
`render.py:496-527` (one global token in every `preview-<projectId>` route, no header removal). Breaks the 0023
G18b preview premise and the claim "The preview is not public" (`docs/architecture/prototype-deployment-flow.md:62,
82`). Abuse: tenant A deploys a site that logs request headers. On A's next promotion the probe delivers the
platform-wide `PREVIEW_PROBE_TOKEN` to A's candidate (and to A's live site). With it, A requests
`<victim>.preview.<site domain>` with the header and sees the victim's candidate: a release not yet published, or
a failed candidate (the preview route is never deleted, so it stays reachable). The new certificate name
`*.preview.<site domain>` makes this work over trusted HTTPS too. Pre-existing since task T, not introduced by
Task 12, but inside this brief's question "does the probe's preview token leak": it does. Fix (any one closes
the cross-tenant part; the first is smallest):
1. Per-project token: send and match `HMAC-SHA256(PREVIEW_PROBE_TOKEN, projectId)` (base64url) instead of the raw
   secret, in `render_preview_route`, `_matches_token` and `_answers_once`. A tenant then only learns the token of
   its own preview.
2. Send the header only on preview probes (`probe_site` gets a `preview: bool`), not on the site-host probe at
   `routing.py:681`.
3. Optionally strip it before the backend: a `RequestHeaderModifier` `remove: [X-Webblock-Preview]` on the
   preview rule (filters run after the match); this needs `site-route-shape.yaml:106-112` to admit exactly that
   filter.
Add a unit test that the header value differs per project and is absent from site-host probes.

**S2. No database guard keeps sub-domains unique, and the takeover guard now rests on that.**
`src/migrations/00022_notion_column_order_and_rules.sql:111-155` (project_hosting has a CHECK on the label, no
UNIQUE on `subdomain`); the value comes from Project's event (`presentation/messaging/v1.py:224-231`). Breaks
the G18/G18a takeover guard ("refuses a host that is not the project's own"). Abuse: if a Project bug, a forged
Project event (stolen broker publisher credential) or the `provisioning_hosting_intake` login inserts a second
hosting row with victim's sub-domain, every later step is honest and still hands over the host: the bootstrap
labels the new namespace with victim's sub-domain, the worker renders that host, and `site-route-shape.yaml:95`
admits it because the label matches. Two routes then share one hostname; the older wins, so a victim not yet
published (or whose route is newer) loses its site to the attacker, and preview probes cross as in the
validator's I2. Before Task 12 the cluster could check the host against the namespace name; now it cannot, so
the database is the only place to check it. Fix: migration 00024 with
`CREATE UNIQUE INDEX project_hosting_subdomain_key ON provisioning.project_hosting (subdomain)`, with an upgrade
test and its change report. G20 allows it: it is an index that enforces a rule Notion states ("2 · Project":
unique over all projects, deleted ones included; hosting rows are never deleted, so deleted projects stay
covered). The intake should then treat a unique violation as a permanent failure (dead-letter, alert), never
retry. **Owner**: the validator read G20 as forbidding this; I read G20 as allowing it. Needs the owner's yes.

### Low

**S3. The renderer does not check the preview host (defence in depth).** `render.py:505-527`: any valid host is
accepted for `preview-<projectId>`; only admission refuses a wrong one (validator M3, still open after
`a790542`). Breaks nothing today (the worker computes the host), but a worker bug is caught only in the cluster.
Fix: pass `subdomain` and `site_domain` to `render_preview_route` and refuse anything but
`<subdomain>.preview.<site domain>`, as `_check_owned` does for the site route; add a unit test.

**S4. The route policy does not check the preview route's match.** `site-route-shape.yaml:73-112` checks names,
parents, hosts, backends and filters but not `rules[].matches`. A stolen worker token (or a renderer bug) can
write `preview-<projectId>` without the header match, so the candidate is public on its preview host. The
content is the tenant's own and the worker can already repoint the live route within the namespace, so the
impact is small. Fix: for `isPreview`, require every rule to have exactly the one Exact match on
`X-Webblock-Preview` (value not checked, or checked for length).

**S5. Site domain length allows hosts over 253 characters.** `routing.py:120` caps `site_domain` at 200, but
`<63>.preview.<site domain>` needs at most 181 (and `<63>.<site domain>` at most 189). With a long site domain,
long sub-domains fail every promotion with `route_refused` (availability, not a leak). Fix: cap at 181 in
`RoutingSettings.validate` and in the worker config check, with a test.

**S6. `tenant-writer` names the catalog-secret mark but not the sub-domain label.** `tenant-writer.yaml:113-117`
protects the mark by name "so it holds even if that rule is widened"; the sub-domain label, which the takeover
guard now trusts, is protected only by the general rule at lines 118-122. Fix: add the same named validation for
`websites.webblock.internal/subdomain` (only the bootstrap sets or changes it), with a Kind case.

**S7. Look-alike names (owner, Project side).** Every check accepts `xn--` labels (punycode), so `xn--pple-43d`
(a Cyrillic look-alike of `apple`) is a valid sub-domain, and `preview` is a valid sub-domain whose site host
`preview.<site domain>` sits above every preview host (cookies set by that site with `Domain=preview.<d>` reach
the preview hosts; only the probe visits them, so no harm today). Not a takeover. Fix, for the owner to pass to
Project: reserve `preview` and decide whether to refuse `xn--` labels; for production, list the site domain on
the Public Suffix List so one tenant cannot set cookies for all.

## Checked and clean

- Takeover between tenants by host form: site host has one label before the site domain, preview host two, so no
  site host can equal any preview host (G18b fixes I1 and I2). Route policy compares exact strings
  (`site-route-shape.yaml:95`); the HTTPRoute CRD refuses upper case, trailing dots and wildcards, the label regex
  (`bootstrap-scope.yaml:137`), the DB CHECK and `render.py:129` are all lower-case one-label, so case, trailing
  dot and long-label tricks are refused before or at admission.
- Missing label: no attached route is admitted (`site-route-shape.yaml:92-94`); missing site domain or project
  refuses all (line 74); missing params ConfigMap denies (binding `parameterNotFoundAction: Deny`).
- Label edits: only the bootstrap may write tenant namespaces (`tenant-writer.yaml:118-122`); the bootstrap may
  set the label at creation or add it once, never change or remove it (`bootstrap-scope.yaml:133-139`); the DB
  value is set once too (`00020` trigger, lines 285-294). The delivered-mark apply keeps the label
  (`bootstrap.py:147-152`).
- Stolen identities: worker alone cannot change labels (RBAC `20-tenant-worker.yaml`, namespaces `list` only)
  and cannot name another tenant's host; bootstrap alone can create namespaces with any label but cannot write
  routes (`tenant-writer.yaml:113`); the build worker and the API hold no Kubernetes token
  (`deployment-build-worker.yaml:1`). A cross-tenant takeover needs both cluster identities, or the DB gap in S2.
- 00023 grant: one column, `SELECT (subdomain)` only; migrator and missing-role guards; no Down; the bootstrap
  writes nothing (`test_schema_00023.py`, per the validator's run). Least privilege: yes.
- Wildcard certificate: `*.<d>` and `*.preview.<d>` only, private key in `webblock-gateway` (no tenant access),
  rotated each issue; wildcards keep tenant names out of CT logs. The listeners' suffix match covers both.
- Probe transport: Gateway URL must be https (`config/worker.py:520-523`), TLS verified against the local CA,
  SNI set per host, no redirects followed, body bounded; token held as `Secret` with `repr=False`; inventory
  returns names only, not route specs (`site_cluster.py:679-690`).
- Errors and messages: every policy message is fixed text with no request value.
- Secrets: no value in the diff; the example token in `deploy/prototype/manifests/preview.yaml` is marked as an
  example.

Verdict: needs fixes (S1 and S2; S2 needs the owner's yes on the unique index under G20).
