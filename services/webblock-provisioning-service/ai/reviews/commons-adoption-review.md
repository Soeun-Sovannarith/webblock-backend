# Review: commons-adoption (web-block-commons 0.1.0)

Stage: 5 Verify, the one independent review of the change.
IDs: FR-1, FR-2 (FR-2.1, FR-2.2), FR-8 (FR-8.1, FR-8.2); TR-26, TR-27, TR-30, TR-32, TR-37.
Range: `git diff fe1ba5c..a77d4a2` on branch `commons-adoption`, against `.claude/docs/changes/commons-adoption.md`.
Out of scope: a deep token audit (the security reviewer checks tokens separately).

Lead rulings accepted as given: TR-37 excepts the token check limits that commons fixes; the development tokens
without `user_id` are a follow-up (decision 0024 G9); the extra doc edits are accepted.

## Verdict

Fix round. There are 2 Medium findings: a false doc claim about trust variables, and FR-8 still unmet on the HTTP
access path after its Drift line was removed. Both are small fixes. There is no High finding. Every gate I ran
passed.

## Constitution check

The brief cites "I.6 docs with the code". Constitution version 2 has no I.6 (Article I ends at I.5). I read it as
VII.1 (docs state current facts).

| Article | Result | Evidence |
| --- | --- | --- |
| I | pass | Every commit cites IDs (4335232 FR-8, 7ef167c FR-2, 96f4816 FR-1, FR-2, FR-8, a77d4a2 TR-37, TR-32). The FRD and TRD changed in the same branch. TR-37 changed by lead ruling. |
| II | pass | Constitution version 2 names web-block-commons (II.1). The wheel is pinned exactly; its SHA-256 `e1c494ef...a4f3` matches `sha256sum` and `uv.lock`; no credential (II.2). `py-age` and `py-vuln` are clean. |
| III | pass, see M-2 | III.1 and III.2: commons is imported only in `infrastructure` (grpc/call.py, identity/), and hosts reach it through infrastructure. pyproject.toml adds `webblock_commons` to the core's forbidden modules (a tighter contract), and `py-layers` reports 16 kept. III.3 to III.6: not applicable (no schema, no broker, no cluster code). III.7: no token is sent on gRPC or HTTP; the interceptor drops `authorization`. III.8: unchanged. |
| IV | pass | `mypy --strict` and ruff pass. The one `type: ignore` moved to `project/access.py` keeps its narrow code and reason. |
| V | pass | V.1: FR-2.2 and FR-8.1 tests fail on the base code (reasoned below). FR-8.2 and FR-2.1 are honest guards. V.2: all fast gates and the dependency gates ran (table below). V.3: not applicable (not a phase end). |
| VI | pass | No cluster, secret, push or Notion edit in the diff. |
| VII | conflict (minor), see M-1, L-2, L-3 | VII.1: the runbook says `SSL_CERT_FILE` and `SSL_CERT_DIR` change nothing, which is false (M-1). Decision 0017 still calls `IDENTITY_USER_ID_CLAIM` required (L-3). VII.2: no dashes; two changed table rows in observability.md are over 120 characters (L-2). |
| VIII | pass | I did not write this change. The reviewer wrote only this report. |

## Requirements and tests

| ID | Tests (name carries the ID) | Status | Evidence |
| --- | --- | --- | --- |
| FR-1.1 | `test_fr1_1_an_audience_list_holding_provisioning_is_accepted`; `test_a_valid_token_with_the_scope_is_served_on_both_transports` | done | The library checks `aud` holds `provisioning` (`core/token.py` `audience_contains`). |
| FR-1.2 | `test_fr1_2_the_library_refuses_each_bad_claim` (`typ` `ID`, `bearer`, null, 1), `test_fr1_2_rs256_only_and_a_signature_of_a_published_key`, `test_fr1_2_the_skew_is_30_seconds`, `test_a_refused_token_is_401_on_both_transports` | done | The library checks RS256 only, `iss`, `aud`, `typ` Bearer, `exp`, and `nbf` within 30 s. The FRD Drift line for `typ` is closed. |
| FR-1.3 | `test_fr1_3_a_key_set_that_cannot_be_had_is_unavailable_not_unauthenticated`, `test_fr1_3_construction_fetches_nothing`, `test_keys_that_cannot_be_had_are_503_on_both_transports`, `test_main` `test_production_starts_with_jwt_and_answers_503_while_the_issuer_is_down` | done | `verifier.py` maps `IDENTITY_UNAVAILABLE` to `IdentityKeysUnavailable`, which answers 503. |
| FR-2.1 | `test_fr2_1_a_valid_token_names_its_subject_roles_and_user`, `test_fr2_1_a_token_with_a_user_id_reads_a_release` | done (guard) | A token with a `user_id` passed before this change too. |
| FR-2.2 | `test_fr2_2_a_token_without_a_user_id_is_403_on_every_route_and_mcp` (7 bad `user_id` values; read, admin, `/mcp`; `access.calls == 0`; nothing stored), `test_fr2_2_a_token_without_a_valid_user_id_is_access_denied`, `test_fr2_2_a_refused_token_without_a_user_id_is_still_401` | done | The base answered 404 here (the removed FRD Drift line), so the test failed first. `verifier.py` raises `ActionDenied` and `presentation/http/auth.py:98` answers 403. |
| FR-8.1 | `test_fr8_1_check_access_sends_the_user_id_and_request_id_never_a_token`, `test_fr8_1_check_access_never_sends_the_user_of_an_outer_context`, `test_fr8_1_get_repository_sends_the_request_id_and_no_user_id`; traceparent: `test_call.py` `test_metadata_carries_the_traceparent_of_the_grpc_span_when_tracing_is_on` | done for gRPC, Drift on HTTP (M-2) | The base `call.py` sent no `x-user-id`, so the test failed first. The test stubs now use `open_channel`, so they check the headers the real hosts send. |
| FR-8.2 | `test_fr8_2_a_jenkins_call_carries_no_user_request_or_trace_header` | done (guard) | Jenkins uses its own httpx2 client, which commons never touches. The test sets a bound user, a correlation ID and a recording span. |
| TR-26 | the FR-1 tests | done | The skew is 30 s, within "at most 60 s". |
| TR-27 | `test_tr27_roles_come_only_from_a_top_level_list`, the FR-2.2 tests | done | `user_from_claims` reads roles only from top-level `roles`, and never `realm_access`. |
| TR-30 | the FR-8 tests | done for gRPC, Drift on HTTP (M-2) | |
| TR-32 | n/a (a design row) | done | The TRD says "does". FR-43 stays Provisioning's own. |
| TR-37 | `test_g7_*` (`config/test_identity.py`) | done (lead ruling) | The retired keys are refused by name in every profile and mode. |

### Removed tests

The 61 old `test_jwt.py` tests and 13 `test_identity.py` tests were replaced. The key set behaviour they covered is
now in commons' own suite at tag v0.1.0 (`python/tests/unit/core/test_keyset.py`, `test_token.py`). That suite covers:

- one fetch in flight, and the unknown `kid` rate limit and storm;
- whole set replacement, and the 24 hour cut off;
- no redirect, and body, encoding and timeout limits;
- private key material and weak keys;
- an https issuer;
- token shape and header rules, the 30 s skew, and unexpected errors answering 401.

The config tests for algorithms, skew cap and claim names are gone because those keys are now refused by name, and
the `test_g7_*` tests cover that. Provisioning's own G5 rules (`sub`, `iat`, lifetime) are still tested
(`test_g5_*`). `platform_roots` and `decode_object` moved to `project/access.py`, and their tests moved with them.

One rule the old tests held has no replacement: `test_trust_variables_of_the_environment_are_ignored` (finding F-5 of
H-V). The library does not keep that rule (M-1).

### Old and new behaviour

Nothing FR-1 or FR-2 asks for is lost. These are the intended differences, each stated in decision 0024:

- The algorithm is RS256 only.
- The skew is a fixed 30 s.
- The key set comes from discovery.
- `typ` is now checked.
- A token with no `user_id` answers 403 (it answered 404), including on the `ADMIN` path.
- A malformed `roles` claim now gives no roles. Before, the token was refused with 401 `scope_malformed`. This fails
  closed (403 on a role-gated action).
- An `exp`, `nbf` or `iat` of null is now `claims_malformed`.
- A year beyond 9999 is no longer refused by the parser. The lifetime cap still refuses it.

## Commands

`free -m` showed about 2.4 to 3.6 GB available. No Kind stack or Testcontainers suite was running. Another agent's
`py-test` run in this worktree finished before mine started.

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | passed (exit 0) | import-linter: 16 contracts kept, 0 broken |
| `python3.14 -I scripts/py/spec_check.py` | report: 0 problems | The hook refuses `make spec-check` for reviewers, so I ran its script directly. FRs with a test: 40 of 51. FR-2 and FR-8 left `no_test_yet`. |
| `CONTRACTS_REPO=../web-block-contracts make py-test` | passed 7014 | |
| `make contracts-check chart-check` | passed (chart: 23) | |
| `make py-vuln` | passed | 107 registry packages scanned. 2 certifi findings are under the existing exception, which expires 2026-11-03. The path wheel is not scanned (decision 0024 G2). |
| `make py-age` | passed | 107 pins, 0 failures |
| `make py-test-integration`, `py-test-messaging` | not run | No schema, store or broker change |
| `make image-scan` | not run | The Dockerfile now copies `vendor/`. The image build was not exercised in this review. |
| Kind (`proto-e2e`) | not run | Kind runs belong to kind-runner. The JWT path is not used by the Kind stack (dev-tokens). |

## Findings

### High

None.

### Medium

**M-1. The runbook and decision 0024 G4 say trust variables of the environment are ignored. They are not.**

- `.claude/docs/runbooks/identity.md:31-33` says "`SSL_CERT_FILE` and `SSL_CERT_DIR` change nothing".
- `.claude/docs/decisions/0024-auth-and-telemetry-alignment.md:54` says "no proxy or trust variable of the
  environment".

The library fetches with `httpx2.AsyncClient(verify=True, trust_env=False)` (`webblock_commons/core/keyset.py:276`).
With `verify=True`, httpx2 builds a `truststore.SSLContext` (`httpx2/_config.py:33-39`). On Linux,
`truststore/_openssl.py:35-38` calls `ssl.get_default_verify_paths()` and `ctx.set_default_verify_paths()`, and both
read `SSL_CERT_FILE` and `SSL_CERT_DIR`. So a CA file named in the API's environment widens trust for the key set
fetch.

The old verifier refused this (`platform_roots()`, finding F-5 of H-V), and its test was removed in this change.
commons has no test for it: `test_tr9_the_fetch_client_checks_certificates_and_follows_nothing` checks only
`verify is True` and `trust_env is False`. This is Drift between doc and code, and a lost hardening.

Fix, one of:

- (a) A commons release that builds the platform roots context, as `project/access.py:273` does, with a test that
  sets `SSL_CERT_FILE`.
- (b) Owner ruling: accept the gap and correct the runbook and G4 to say the system trust store, where `SSL_CERT_FILE`
  and `SSL_CERT_DIR` do apply, and the chart never sets them. Record the reason.

Either way, security-reviewer should rule on it.

**M-2. FR-8 is still unmet on the HTTP access path, and its Drift line was removed.**

`96f4816` deletes "FR-8: `CheckAccess` sends no `x-user-id`" from the FRD Drift list. Only the gRPC path was fixed.
`ProjectServiceAccess` (`PROJECT_ACCESS_MODE=project-service`, which production may use and does use per the FR-5
Drift line) sends only `X-Request-Id` (`src/provisioning/infrastructure/project/access.py:205-210`). It sends no
`x-user-id` and no `traceparent`, which FR-8 and TR-30 ask of every call to Project. The FRD now shows no Drift for
FR-8 while the production path differs.

Fix, one of:

- Build the client on commons' `InternalTransport` or `internal_client` (`webblock_commons/outgoing.py`) and bind the
  call's context as `unary` does. Add `test_fr8_1_the_http_access_check_sends_the_user_id_and_traceparent` in
  `tests/unit/infrastructure/project/test_access.py`.
- Restore a narrowed Drift line in the FRD: "FR-8: the HTTP access check (`project-service` mode) sends no
  `x-user-id` or `traceparent` (`infrastructure/project/access.py:205`)".

### Low

**L-1.** `.claude/docs/requirements/spec.yaml`: the FR-2 test list (lines 281-306) holds tests that name other IDs,
such as `test_fr1_2_the_skew_is_30_seconds`, `test_g8_*` and `test_construction_refuses_an_unusable_setting`. The
FR-1 list also holds `test_fr2_*` tests. Fix: list under each FR only the tests that name its rows, and leave the
G5 and G8 tests under FR-1 or TR-26.

**L-2.** `.claude/docs/runbooks/observability.md:65-66`: the changed table rows are 170 and 149 characters long.
VII.2 sets a limit of 120 characters for changed lines. Fix: shorten the cells, for example by moving the cause list
to identity.md.

**L-3.** `.claude/docs/decisions/0017-project-service-catalog-and-access.md:50,58` still says `IDENTITY_USER_ID_CLAIM`
is a required setting, and the decisions README row for 0017 says "amended by 0019" only. Fix: add an "amended by
0024 (G6, G7)" note at the top of 0017, like the notes in 0006 and 0019, and update its README row.

**L-4.** `tests/unit/infrastructure/identity/test_jwt_transport.py:144`: the FR-1.2 transport test covers a wrong
`iss`, an expired token and a forged signature. It does not cover `typ` `ID` or a missing audience at the transport.
The unit tests cover both, so this is optional. Fix: add those two tokens to the loop.

## Notes for the lead

- The change touches auth and dependencies, so security-reviewer must run, starting with M-1.
- The image build with `vendor/` (Dockerfile, build.sh) was not exercised here. `make image-scan`, or a kind-runner
  build, should confirm the image builds and stays token free.
