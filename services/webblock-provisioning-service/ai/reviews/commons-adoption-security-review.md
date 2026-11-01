# Security review: web-block-commons adoption (token check, inner call headers, vendored wheel)

Stage 5 (Verify). Branch `commons-adoption`, range `fe1ba5c..a77d4a2` (commits 4335232, 7ef167c, 96f4816, a77d4a2).
Reviewer model: Claude Opus 5.5 (`claude-opus-5-5`). IDs in scope: FR-1, FR-2, FR-8; TR-26, TR-27, TR-30, TR-32,
TR-37; constitution II.2 (version 2), III.7, III.8. Measured against the change note
`.claude/docs/changes/commons-adoption.md` and decision 0024.

## Method

Skills: `differential-review` (risk-first triage, removed-code history, adversarial pass done by the reviewer, since
no `adversarial-modeler` agent exists here) and `sharp-edges` (library settings and defaults).

Read: the whole code diff (`src/`, `tests/`, `deploy/image/`, `pyproject.toml`, `uv.lock`, `.gitignore`,
`.env.example`), the changed docs (decisions 0006, 0024, runbook `identity.md`, TRD), and the library at tag v0.1.0
(`core/verifier.py`, `token.py`, `keyset.py`, `settings.py`, `user.py`, `errors.py`, `context.py`, `grpc/client.py`,
every `LOG` call). Compared the removed `jwks.py`, `verifier.py` and `token.py` (at `fe1ba5c`) cause by cause with
the library's checks.

Commands and results:

- `sha256sum vendor/webblock_commons-0.1.0-py3-none-any.whl`: `e1c494ef...4a4f3`, equal to the brief, to
  `uv.lock` (`wheels` entry) and to decision 0024 G1.
- Wheel against the tag: read in memory (the reviewer hook forbids scratch writes, so no unzip to disk) and each
  member compared byte for byte with `git show v0.1.0:python/src/<member>`. 43 files equal, none only in the wheel
  or only in the tag; the extra members are directory entries and `dist-info` (METADATA, RECORD, WHEEL). No `.pth`,
  no entry points. METADATA pins equal ours. Tag v0.1.0 is annotated, on `main`, commit b6d1518. Not imported, not
  run.
- Secret scan of the diff (private key headers, JWTs, GitHub tokens, `password=`, `--build-arg`, `UV_INDEX`) and of
  every wheel member (also URLs with credentials): nothing found. Test keys are generated at run time
  (`tests/unit/infrastructure/identity/issuer.py:52`).
- Trust roots experiment, stdlib only (`.venv/bin/python -I`): an `SSLContext` after `set_default_verify_paths()`
  holds 118 CAs, and 1 CA with `SSL_CERT_FILE=<one cert>`. The plain `SSLContext` ignores `SSLKEYLOGFILE`.
- `CONTRACTS_REPO=... make py-fmt-check py-lint py-typecheck py-layers py-test`: 631 files formatted, ruff clean,
  mypy clean on 625 files, import-linter 16 kept and 0 broken, 7014 tests passed.
- `make py-vuln`: 107 registry packages scanned offline, 2 findings (certifi, both under the existing exception
  that runs until 2026-11-03; not part of this change and not made worse: the library's fetch does not use certifi).
- `make py-age`: 107 lock pins, 10 tool pins, 1 image pin, 0 failures. The path wheel is not a registry package
  and is not counted (decision 0024 G2, II.2 version 2 exemption).

Not checked, and why:

- Whether the wheel equals the GitHub Release asset: no network access to the release from here.
- Whether uv verifies the path wheel's hash under `UV_FROZEN=1` (the Dockerfile) rather than `--locked` (decision
  0024 G1 says `--locked`): testing it needs a modified wheel on disk, which the reviewer may not write. The wheel
  and the lock come from the same commit, so the hash guards against drift, not against a hostile commit.
- `make image-scan`: the image's packages change (a new wheel in the venv). Not run by the brief; the lead runs it
  before the merge.
- The chart's egress rule to the issuer on 443: a known follow-up named in the brief.

## Findings

### M-1 (medium): the key set fetch again trusts `SSL_CERT_FILE` and `SSL_CERT_DIR` (regression of H-V finding F-5)

- Where: `src/provisioning/infrastructure/identity/verifier.py:70-73` builds the library's `TokenVerifier` without
  an `ssl_context`. The library then fetches with `httpx2.AsyncClient(verify=True, trust_env=False)`
  (`webblock_commons/core/keyset.py:274-279`). For `verify=True`, httpx2 builds a `truststore.SSLContext`
  (`httpx2/_config.py:41`), and truststore on Linux calls `ssl.get_default_verify_paths()` and
  `set_default_verify_paths()` (`truststore/_openssl.py:35-36`). OpenSSL reads `SSL_CERT_FILE` and `SSL_CERT_DIR`
  there, so `trust_env=False` does not stop them. The experiment above shows `SSL_CERT_FILE` replacing all 118 roots
  with the one CA it names.
- What was removed: `platform_roots()` (old `verifier.py:225-247`), which loaded OpenSSL's compiled-in CA file and
  directory by name, turned off `SSLKEYLOGFILE` and refused to start with no roots. Its key set test went too: old
  `tests/unit/infrastructure/identity/test_jwt.py:762` ("SSL_CERT_FILE or SSL_CERT_DIR naming the issuer's CA does
  not make it trusted"). The copy kept in `infrastructure/project/access.py:271-296` now protects only the Project
  REST client.
- What it breaks: TR-26 and FR-1 (the API checks each token against the issuer's key set) and III.7 (the
  gateway, then the first service, checks each token), both through the trust behind that key set. It also breaks
  VII.1: three docs state the opposite of what now happens. `.claude/docs/runbooks/identity.md:31-33` says
  "`SSL_CERT_FILE` and `SSL_CERT_DIR` change nothing". Decision 0024 G4 (line 54) says "no proxy or trust variable
  of the environment". Decision 0006 section 5 (lines 186-189) still says they "have no effect" and that the API
  refuses to start with no roots. The 0024 amendment note does not withdraw that sentence.
- Abuse: someone who can set the API's environment (the chart reads the API ConfigMap whole through `envFrom`,
  `deploy/helm/provisioning/templates/deployment-api.yaml:30`, so a values or ConfigMap edit is enough) and who can
  place a CA file in the pod and intercept traffic to the issuer can do the following. They serve the API a
  discovery document and key set signed under their own CA, then mint tokens with any `user_id` and
  `roles: ["ADMIN"]`. Such a token passes every check. ADMIN skips the Project owner check for rollback (decision
  0019 section 7), so it can roll back, suspend or resume any project. Exploitability is low, since it needs
  deployment-level control plus a network position. The impact is a full authentication bypass. The rating is
  medium because this restores an issue that was already fixed and the docs now promise the fixed behaviour.
- Fix (choose one, then restore the test):
  1. In Provisioning now: move `platform_roots()` to one shared adapter module (for example
     `infrastructure/tls.py`, used by both `project/access.py` and the verifier, so the rule has one copy). Pass
     `ssl_context=platform_roots()` from `JwtVerifier.__init__` to `TokenVerifier`. The library accepts it; its
     docstring says "tests only", so record the reason in decision 0024. This also brings back the start-up refusal
     when no roots exist.
  2. In the library: release 0.1.1 where `KeySetSource` builds its context from `openssl_cafile` and
     `openssl_capath` by name, with `keylog_filename = None`. Then move to it in one reviewed change.

  In both cases, add back a `test_tr26_...` that sets `SSL_CERT_FILE` and `SSL_CERT_DIR` to a CA that signed the
  test issuer's certificate and asserts that the fetch fails (503 `IDENTITY_UNAVAILABLE`). Make the runbook and
  0024 G4 true again. Until then, change the docs to say what happens now.

### L-1 (low): unused production scope-claim constants for a retired setting

- Where: `src/provisioning/infrastructure/config/identity.py:80-85` (`PRODUCTION_SCOPE_CLAIM`,
  `SCOPE_CLAIM_IN_PRODUCTION`). Nothing reads them, and their text still describes `AUTH_SCOPE_CLAIM`, which is now
  refused by name (G7).
- What it breaks: `.claude/rules/python-code.md` ("Delete code that nothing calls") and VII.1 (current facts). It
  has no security effect today. A later reader could take the text as a production rule that still holds.
- Fix: delete both constants.

## Checked and clean

- Wheel integrity (II.2): the hash equals the brief, the lock and 0024 G1; the contents equal tag v0.1.0
  (annotated, on `main`); no credential in the wheel, `uv.lock`, `pyproject.toml` or the Dockerfile.
- Token check (FR-1, TR-26): `iss` exact (`keyset.py`/`token.py:check_claims`). `aud` holds the configured audience
  as a whole element. `typ` exactly `Bearer` (tests cover `ID` and `bearer`). `alg` exactly `RS256` in the header,
  and PyJWS is limited to `[RS256]`. `jku`, `jwk`, `x5u`, `x5c`, `crit`, `zip` and `enc` are refused. `exp` is
  required, and `exp` and `nbf` are checked with 30 s of skew. The signature is checked against keys of the fetched
  set only, RSA of at least 2048 bits. A key set holding private members is refused whole.
- Provisioning's own checks (G5): `token.py:32-62` checks `sub` (present, a valid requester), `iat` not in the
  future, `exp` after `iat` and `nbf`, and the lifetime cap from `iat` and from now. They run after the library and
  before `user_id`, so a refused token stays 401 (`test_fr2_2_a_refused_token_..._still_401`).
- Key set source: `jwks_uri` comes from discovery, whose `issuer` must match. It must be https on the issuer's
  scheme, host and port. No redirects, no proxy, `Accept-Encoding: identity`, 5 s total and 2 s to connect for
  discovery and keys together, 1 MiB streamed with `Content-Length` checked first. At most one fetch per 30 s (so
  random `kid`s cannot drive fetches), concurrent callers share one fetch, and a failed fetch keeps the cached set.
  Keys older than 24 h are not used; previously this was a setting up to 7 days, example 1 h. The 24 h is fixed by
  decision 0024 G4 and excepted in TR-37 by the owner, so it is not raised. No key set answers 503, never 401.
- User and roles (FR-2, TR-27): `user_id` must be a canonical lowercase non-nil UUID string; any other value is
  `ActionDenied` and 403 `ACCESS_DENIED` (`presentation/http/auth.py:98-99`, `operations/errors.py:322`). This
  happens in `Authenticate`, before any store or Project call. `/mcp` is covered too: the test asserts no access call
  and no stored command (`test_jwt_transport.py:247-262`). Roles come only from a top-level list of text, and
  anything else gives no roles. `realm_access` is never read (`test_tr27_...` with `realm_access.roles: [ADMIN]`).
  `ADMIN` is compared whole and with case.
- 404 for a non-owner (III.8): the CheckAccess mapping is unchanged by this diff. A principal without `user_id`
  still gets no project access.
- Inner calls (FR-8.1, TR-30, III.7): every channel is the library's `internal_channel`
  (`call.py:51-55`; all three `open_channel` callers). The interceptor drops `authorization`,
  `proxy-authorization`, `cookie`, XFCC, the dev caller header and our own headers, then adds `x-request-id`,
  `traceparent`, `tracestate` and `x-user-id`. `unary` binds a fresh context per call, so an outer user never leaks:
  `GetRepository` and `GetEntitlement` pass none (`test_fr8_1_...outer_context`, `test_fr8_1_get_repository_...`).
- Outside hosts (FR-8.2): Jenkins uses its own httpx2 client; the test runs inside a bound user context with a
  recording span and finds none of the four headers. The key set fetch sends only `Accept` headers. The Kubernetes
  client is untouched.
- Logs (G8, III.7): the library logs only fixed causes. The bridge (`log_bridge.py:106-120`) keeps the cause up to
  `:`, copies no `extra` fields and no `exc_info`, and swallows formatting errors. `verifier.py` logs fixed causes
  only. Tests assert that no token, claim or key reaches the log (`test_g8_no_token_claim_or_key_reaches_the_log`)
  and that every cause is one the metrics count.
- Retired settings (G7): all 10 are refused by name in every profile and mode (`config/identity.py:62-76,132`;
  `test_g7_*`). `AUTH_ISSUER` must be https without credentials or a fragment, and the library checks it again.
- Image and build: `COPY vendor/` is in the build stage only, and the runtime stage copies only `/opt/venv` and
  `/app/src`. `build.sh` copies only tracked files of `vendor/`. No `ARG` other than `BASE_IMAGE`, no secret mount.
  `.gitignore` re-admits only `vendor/*.whl`.
- Layers (III.2): `webblock_commons` is added to the core's forbidden modules (`pyproject.toml:491-492`) and is
  imported only in `infrastructure/grpc/call.py` and `infrastructure/identity/`. Import-linter: 16 kept.
- Cluster identities (III.6): the diff does not touch `deploy/helm`, RBAC or policies.
- Accepted and not raised: development tokens without `user_id` (0024 G9); API egress to the issuer on 443 (chart
  follow-up).

Verdict: needs fixes (M-1; L-1 can go in the same round).
