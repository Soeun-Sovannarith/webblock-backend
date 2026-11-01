# 0018: Errors are RFC 9457 Problem Details

**Amended by decision [`0023`](0023-phase-c-rest-shape-and-admin.md) (G5):** `requestId` and `traceId` replace
`correlation_id`, `commandId` replaces `command_id`, `errors[]` is added, codes are UPPER_SNAKE, and the extensions are
camelCase. This record is implemented by Phase C.

Status: **Current; decided by the owner** (given in the Project service's lead session, together with the same change
in the Project service). The shared rule is in the Project service's
`.claude/docs/contracts/service-communication.md` (HTTP conventions, "Errors") and its design spec section 6.3. The
service implements it as amended by `0023`.

## 1. The error body

Every REST error response (every 4xx and 5xx, from every path that writes one: the invoker, the Starlette 404/405 and
422 handlers, authentication, and the middleware for panics, the in-flight cap, the request timeout and the body
limit) and every MCP tool result marked as an error carries one RFC 9457 Problem Details object instead of today's
flat `Error`:

```json
{
  "type": "urn:webblock:problem:operation-in-progress",
  "title": "Another command of the project is in progress.",
  "status": 409,
  "instance": "/v1/projects/…/releases/…/deployments",
  "code": "operation_in_progress",
  "correlation_id": "…",
  "command_id": "…"
}
```

| Member | Value |
| --- | --- |
| `type` | `urn:webblock:problem:` + the code with `_` replaced by `-`. One URN per code; an identifier, not a page |
| `title` | The code's fixed message (`presentation/operations/errors.py`, `_MESSAGES`) |
| `status` | The HTTP status of the code's class (decision [0006](0006-transports-identity-and-exposure.md) section 3, unchanged); the same number on MCP |
| `instance` | REST only: the request path without the query string. Absent on MCP |
| `code` | Extension: today's stable code (`invalid_id` … `internal`), unchanged |
| `correlation_id` | Extension: as today |
| `command_id` | Extension: only with `operation_in_progress`, as today |

- **No `detail` member**. The body never carries provider error text, tokens, customer content or anything
  the caller sent, as today's `Error` promises.
- **Field case**: extensions keep this API's snake_case. The Project service uses camelCase (`requestId`,
  `traceId`) for its own; the standard members are identical in both services.
- **MCP**: an error tool result's structured content is the same object (`tools.json` already says a tool's
  result is the REST operation's body). JSON-RPC transport errors raised by the MCP SDK are unchanged.

## 2. Consequences

- **Content type.** REST error responses are `application/problem+json`, on the router and the middleware paths
  alike. `WWW-Authenticate: Bearer` on 401, `Cache-Control: no-store` and the security headers, and `Retry-After`
  where it is sent today stay unchanged. Success bodies are unchanged (bare JSON).
- **One builder.** The shared `Outcome` keeps one error body built in one place (`presentation/operations`), used by
  REST and MCP; the transports add only `instance` (REST) and the content type.
- **Contracts.** `src/contracts/openapi.yaml`: the `Error` schema becomes `Problem` (`additionalProperties: false`,
  required `type`, `title`, `status`, `code`; `type` pattern `^urn:webblock:problem:[a-z0-9-]+$`; `code` keeps
  `maxLength: 100`; `instance`, `correlation_id`, `command_id` optional) and every response that referenced `Error`
  references `Problem` with media type `application/problem+json`. `src/contracts/mcp/tools.json` says error results
  carry the same Problem object. The contract tests (`tests/contracts/test_http_conformance.py`,
  `test_mcp_conformance.py`, `test_wire_names.py`) pin the new shape, and the unit, contract and e2e assertions that
  read
  `code`/`message` move to the new members.
- **OD-25** stays open (the UI and agent consumers have still not reviewed the response shapes) with the error shape now
  fixed by this record.
- **Order.** Implemented on a branch from `python` after tasks PC and PA are merged, so it does not collide with them.
  Gates: `make py-test` (unit and contracts), the lint/type gates, and the full `make proto-e2e` in one Kind run.
- **Neither service reads the other's error bodies** (the access-check client reads only a 200 answer), so the Project
  service's and Provisioning's switches can land in either order.

## 3. Amendment

**Content type follows `Accept`, as in the Project service.** This supersedes the "Content type" consequence of section
2 and the plan's "the `Accept` header is ignored". The rule is the Project service's design spec section 6.3, word for
word: "`application/problem+json` whenever the client's `Accept` allows it (absent, `*/*`, `application/*`,
`application/problem+json`); a client that accepts only `application/json` gets the same body as `application/json`;
any other `Accept` (a 406 case) still gets the body as `application/problem+json`."

"Accepts only `application/json`" means the `Accept` header allows `application/json` (q above 0) but none of `*/*`,
`application/*` or `application/problem+json`; the parsing (q-values, wildcards, an unreadable header) follows the
Project service's `ProblemDetailFactory.contentType`, so both services follow RFC 9110's `Accept` grammar and agree on
every row of the shared table (the Project service's `ProblemDetailFactoryTest` rows, plus
`application/json, text/event-stream`). Syntax outside RFC 9110, such as Java-only number forms like `q=0x1p-1`, may
differ, and every answer is still a valid Problem (lead ruling PD-R3). An error is never a
406: it keeps its own status, and the status and the bytes of the body do not depend on `Accept`. MCP tool results carry
no content type and are unaffected; the HTTP-level `/mcp` refusals (401, 400, 413) are REST responses and follow the
rule. In `openapi.yaml` every error response lists `application/problem+json` and `application/json` with the same
`Problem` schema.

Reason: the consumers (UI and agent, OD-25) have not reviewed the shapes. A client that sends `Accept: application/json`
and checks the content type of the response would otherwise see different behaviour from the two services.
