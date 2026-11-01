# Change note: commons-adoption

Change: adopt web-block-commons 0.1.0 for the token check and the identity headers of inner calls.
IDs: FR-2 (FR-2.1, FR-2.2), FR-8 (FR-8.1, FR-8.2); TR-27, TR-30, TR-32.
Agreed: the owner, in the lead's session.

What changes:
- `vendor/webblock_commons-0.1.0-py3-none-any.whl`, equal to the GitHub Release v0.1.0, SHA-256
  e1c494ef409790e3b238460b8f887097647c584a7fbc4e77a3adb47308e4a4f3; `pyproject.toml` path dependency with the
  `fastapi` and `grpc` extras; `uv.lock` records the same hash. Its pins equal ours (fastapi, grpcio,
  grpcio-status, protobuf, httpx2, pyjwt, opentelemetry-api).
- `.gitignore` lets `vendor/*.whl` in; the image build copies `vendor/` before `uv sync`. No token anywhere.
- Identity: the token check goes through commons; a token with no `user_id` answers 403 `ACCESS_DENIED` (today 404).
- Inner calls to Project: `x-user-id`, `x-request-id`, `traceparent` from commons; never the token; nothing of these
  to Jenkins or other outside hosts.

Tests first: `test_fr2_2_*` (no `user_id` is 403, Project never called, `/mcp` too), `test_fr8_1_*` (CheckAccess
metadata has `x-user-id` and `x-request-id`, no `authorization`), `test_fr8_2_*` (a Jenkins call carries none).

Risks: commons' token rules must equal ours (issuer, audience `provisioning`, RS256, exp and nbf, `typ` Bearer,
top-level `roles`, `ADMIN` exact case); `py-age` and `osv-scanner` must accept a path wheel; the image build must
stay token-free.

Out of scope: FR-43 log fields (next change).
Docs touched: TRD TR-32 ("does", not "will"), `.claude/TECH_STACK.md`, decision 0024 (commons adopted), the auth
runbook.
Constitution: II.1 and II.2 pass (version 2 exemption for first-party packages); III.7 pass; V.2 pass when `py-age`
and `py-vuln` accept the wheel, else stop and ask.
