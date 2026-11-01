# Security review: Phase C Task 12, scoped re-review of security fix round 2 (S1 to S7)

Stage 5 Verify, Phase C exception: cites decision 0023 G18, G18a, G18b, G18c, G20a instead of FR and TR IDs.
Date 27 October 2026. Branch `phase-c`, range `e6a112d..266e3ec` (fix commits 9fe709b, 0cdbd58, bf6b039, 1d42e21,
5803af1, a890373, handoff 266e3ec; ea57c74 named in the brief is not in this range, the spec commits 43b46fc and
2552479 are docs only and were skipped). Reviewer: security-reviewer, Claude Opus 5.5. Read only; nothing in the
repository edited or committed.

## Method

- Read: earlier report `ai/reviews/phase-c-task-12-security-review.md`; the full code diff of the range under `src`,
  `deploy`, `scripts`; at head `deploy/prototype/policy/site-route-shape.yaml`, `tenant-writer.yaml`,
  `src/migrations/00024_unique_hosting_subdomain.sql`, `hosting_intake.py` (owner-status intake), `intake_store.py`
  and `release_intake.py` (release intake), `workers/event_commands.py`, `workers/inbox.py`, `workers/messages.py`
  (`failure_of`), `postgres/errors.py`, `site_cluster.py` (`_matches_token`, `_is_preview_host`, `probe_site`),
  `render.py` (`preview_token_for`, `render_preview_route`); decision 0023 G18c and G20a, runbook "Migration 00024",
  the handoff's open items.
- Skills: differential-review and sharp-edges reasoning applied by hand.
- Ran:
  - `uv run pytest -q tests/unit`: 6709 passed.
  - `uv run pytest -q tests/integration/test_schema_00024.py tests/integration/test_owner_status_consumer.py
    tests/integration/test_owner_status_store.py tests/integration/test_routing_store.py`: 57 passed.
  - `uv run pytest -q tests/messaging/test_owner_status_consumer.py`: 4 passed.
  - A throwaway `postgres:18` container (no cluster, not the repo's database; stopped afterwards) to test the
    Postgres behaviour behind N1: a table with a primary key and a second unique index, then pgbench with 16
    clients running `INSERT ... ON CONFLICT (project_id) DO NOTHING` of the same fresh row at the same moment.
    Result: several clients failed with `duplicate key value violates unique constraint` on the second index.
    A sequential replay (one session holds the row uncommitted) does NOT fail: the arbiter pre-check waits. So
    the error appears only when two inserts of the same new row pass the pre-check together.
- Not checked: no cluster or Kind command (brief). The new Kind cases (handoff item 5) are written but not run, so
  Cilium's support of `RequestHeaderModifier` `remove` and the CEL rules are reasoned, not observed.
  `make py-vuln py-age` and `make image-scan` not run: no lock, pin or image change in the range.

## Status of the earlier findings

| ID | Status | Evidence |
|----|--------|----------|
| S1 | Closed | `render.py:506-511` token is `base64url(HMAC-SHA256(secret, str(projectId)))`; the raw secret is never in a route or a request. `site_cluster.py:583` sends the header only to `<label>.preview.<site domain>`; the site-host probe sends none. `render.py:514-517` and the route's second filter remove the header before the backend; `_route_target(preview=True)` (`site_cluster.py:229-240`) refuses to confirm a preview route without that removal. Unit tests cover per-project values and no header on site hosts. A site now never sees any token, and even a leaked one opens only its own preview. |
| S2 | Closed, with new finding N1 | `00024` adds `project_hosting_subdomain_key`; guard stops on duplicates and names only a count; migrator-only guard; one transaction, so a concurrent duplicate between guard and index fails the index build. Owner-status intake maps the violation to `SubdomainTaken`, dead-lettered on first delivery (`event_commands.py:152-162`), log carries the type name only. Release intake: the index refuses the row too (no takeover). The mapping of the violation is too broad, see N1. |
| S3 | Closed | `render.py:529-533, 553-555`: the renderer refuses any preview host but `<subdomain>.preview.<site_domain>`, and checks both parts. |
| S4 | Closed | `site-route-shape.yaml:124-129`: a preview route needs at least one rule; every rule needs at least one match; every match has exactly one header, Exact, `X-Webblock-Preview`, 32 to 256 URL-safe characters. The CRD defaults an empty `matches` to a path-only match before admission, which this rule refuses. Regex and other-header matches are refused. See L1 for one gap left. |
| S5 | Closed | `routing.py:122` and `config/worker.py:502` both cap the site domain at 181; tests at the limit. |
| S6 | Closed | `tenant-writer.yaml:138-142`: a change, addition or removal of `websites.webblock.internal/subdomain` on a tenant namespace (named `wb-*` or labelled tenant before or after) is refused for every identity but the bootstrap; DELETE is exempt as for the mark. |
| S7 | Passed to the owner | Handoff "Open for the owner" lists the three points for the Project team. Correctly not coded here. |

## New findings

### Medium

**N1. A same-project insert race is mistaken for a taken sub-domain, and the owner-status event is dead-lettered.**
`src/provisioning/infrastructure/postgres/hosting_intake.py:247-258` (and the docstring at 179-180, which still
says "a concurrent insert wins, and the event goes on with that row"). Breaks G20a's "SubdomainTaken only when
another project holds the name" and the owner-status enforcement (0023 G17). `ON CONFLICT (project_id) DO NOTHING`
only covers the arbiter index. When two transactions insert the hosting row of the same new project at the same
moment, the loser gets a unique violation on `project_hosting_subdomain_key` instead of DO NOTHING (reproduced
above with pgbench). The two inserts that can race are the owner-status intake (two events of one project, the
consumer runs with concurrency) and the release intake's `_insert_release` (`intake_store.py:454-467`), which
inserts the same row with the same sub-domain. The owner-status side then raises `SubdomainTaken` and the event
goes to the dead-letter queue at once. Abuse or impact: if the lost event is `suspended` and the release intake
won the race, the row stays `online`, so a suspended owner's site goes or stays live until an operator replays
the dead letter. The window is small (both first inserts within milliseconds), but the outcome is an enforcement
miss with a misleading "sub-domain taken" alert. Fix: in `_insert_owner_status_row`, run the insert in a
savepoint (`async with conn.begin_nested()`); on a violation of `project_hosting_subdomain_key`, read
`SELECT project_id FROM provisioning.project_hosting WHERE subdomain = :s`. If it is this project, carry on as
"row already there" (return False, the caller re-locks). Only if it is another project, raise `SubdomainTaken`.
Add an integration test that runs two concurrent first inserts of one project (owner-status vs `_insert_release`)
and expects no dead letter. If the release intake later gets a terminal code (concern 2), apply the same check
there first.

### Low

**L1. The route policy admits the header removal but does not require it.** `site-route-shape.yaml:112-129`. A
stolen worker token or a renderer bug can write `preview-<projectId>` without the `RequestHeaderModifier`, and the
probe's token then reaches that project's own candidate again. Impact is small now: the token is per project
(S1 fix), so a site learns only how to open its own preview, and the worker refuses to confirm such a route
(`site_cluster.py:229-240`). Fix (defence in depth, optional): for `isPreview`, require every rule to carry exactly
that one removal filter, with a Kind case.

**L2. The release intake loops forever on a taken sub-domain, without an alert.** `intake_store.py:454-467`,
`release_intake.py` ERROR path, `workers/inbox.py:309-315`. The violation becomes a generic `DatabaseError`; the
row's attempts grow on each take (`intake_store.py:214`), but `max_attempts` is checked only on the
repository-unconfirmed path (`release_intake.py:447`). So the row is taken again after every visibility timeout,
and each take calls the registry again. No takeover, since the index refuses the row. This is the implementer's
concern 2; see the answer below for the fix.

### Informational

- `_matches_token` (`site_cluster.py:282-291`) compares with `==`, not `hmac.compare_digest`. This is fine. Both
  sides are values the worker computed or wrote; no attacker sends a guess to this comparison. The comparison
  that faces the attacker is Envoy's header match over the network. Against a 256-bit HMAC, timing gives nothing
  usable. No change needed.
- The derivation has no context label (`HMAC(secret, projectId)`). That is fine while `PREVIEW_PROBE_TOKEN` is used
  for nothing else. If the secret ever gets a second use, add a prefix such as `preview:`.
- Rotating `PREVIEW_PROBE_TOKEN` leaves old preview routes on old tokens until their next promotion. Only the
  worker ever knew them, so that is acceptable.
- Runbook "Migration 00024" says "correct the other rows with an administrator". `subdomain` is set-once by the
  00020 trigger, so the fix needs the trigger bypassed or the row deleted. Name the exact step, so an operator does
  not weaken the trigger to get past it. This is an operations matter, not a vulnerability.

## Checked and clean

- HMAC: `hmac.new(secret, str(project_id), sha256)`. `str(UUID)` is the canonical lower-case form, so the probe,
  the route and the status check all use one input. Base64url without padding gives 43 characters, which the
  policy regex admits. The secret must be 32 to 256 URL-safe characters (`site_cluster.py:193`, `render.py:556`).
- Probe: the token is added only when `token is not None` (`site_cluster.py:648-649`). No redirects are followed,
  TLS is checked, and the value is never logged. `SiteClusterSettings.preview_token` stays `repr=False` and the
  config keeps `Secret`.
- Filter admission (`site-route-shape.yaml:112-123`): `RequestHeaderModifier` is admitted only on `isPreview`.
  It must have no `set` and no `add`, and `remove` must equal exactly `['X-Webblock-Preview']`. A site route with
  the removal is refused. The Gateway API CRD's own rules stop a filter from carrying a field of another type.
- tenant-writer: the label rule follows the existing mark rule. `inTenant` covers both names and labels before and
  after the change, so creating, relabelling or untenanting does not get around it.
- Migration 00024: forward only, no Down, no grant change, and the guard message holds no values
  (`test_schema_00024.py`, 7 cases passed).
- Dead-letter path: `SubdomainTaken` is raised `from None`, so no driver text or parameters are carried. The
  consumer logs only `type(exc).__name__`, and the message never names the sub-domain.
- Secrets: no real value in the diff. The golden and example manifests hold the HMAC of a documented example
  secret.

## The implementer's two concerns

1. **The probe chooses to send the token by the shape of the host.** Acceptable. The rule
   (`site_cluster.py:571-576`) needs exactly one non-empty label, then `preview.<site domain>`. A site host is
   `<one label>.<site domain>`, and a label has no dot, so a site host can never match. The probe check is also
   the same shape the admission policy enforces for preview routes. If it ever misjudged a host, the safe thing
   happens: a preview host judged a site host gets no header, so the preview probe fails (availability, not
   leakage). A wrong `project_id` with a right host sends a token that only that project's own route would match,
   so the Gateway answers 404. Optional hardening: `RoutePointer` already knows which probe it makes, so pass
   `preview: bool` and also assert that the host shape agrees. That turns a silent mismatch into a loud error.
2. **The release intake retries a taken sub-domain instead of dead-lettering it.** Not a security hole. The unique
   index refuses the row, so no host changes hands, and the project in question is the one whose data conflicts.
   Still, retrying forever is wrong for a condition that is permanent and possibly hostile (see L2). It costs a
   registry call per visibility timeout and raises no alert. It also hides a sign of a forged or buggy Project
   answer. Recommendation:
   - Make it terminal: fail the inbox row with a named code (for example `subdomain_taken`) and log at error.
   - Adding that code is the owner's catalogue decision.
   - Before calling it terminal, apply N1's own-project check. Today, retrying is the correct way to handle the
     same-project race on this path, and a naive terminal mapping would turn that race into a false failure.
   - Until the code exists, a cap is enough: use `attempts >= max_attempts` on the ERROR path as well.

Verdict: needs fixes (N1). S1 to S6 are closed and S7 is with the owner. L1 and L2 can go to the final fix wave.
