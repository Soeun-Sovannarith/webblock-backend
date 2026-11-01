---
name: fastapi-endpoint
description: Use when adding or changing a REST endpoint (and its MCP tool) of the Provisioning API, or when a
  task touches the operation table, the dispatch, the wire bodies or the error codes. Written for this repo only.
---

# Add or change a REST endpoint

This repo does not use FastAPI the usual way: no request or response models, no `Depends`, no routers per
feature, no FastAPI docs or 422s. One table of operations drives both REST and MCP (TR-3, FR-46), every intent
goes through the command or query bus (TR-54, decision `0025`), and the contract files come first (TR-17,
TR-19). Generic FastAPI guides teach the wrong shape here; follow this file.

## Read first

| What | Where |
| --- | --- |
| Routes, body and query checks, response writing | `src/provisioning/presentation/http/app.py` |
| Bearer check before any route | `src/provisioning/presentation/http/auth.py` (`Authenticate`, `TokenVerifier`) |
| The operation table (method, path, inputs, statuses) | `src/provisioning/presentation/operations/operations.py` |
| Parse, send on the bus, build the outcome | `src/provisioning/presentation/operations/invoke.py` (`_dispatch`) |
| Wire bodies (camelCase, envelope, list page) | `src/provisioning/presentation/operations/wire.py` |
| Cursor and limit | `src/provisioning/presentation/operations/paging.py` |
| Error codes and their order | `src/provisioning/presentation/operations/errors.py` (`Code`, `CLASSES`) |
| RFC 9457 Problem body | `src/provisioning/presentation/operations/problem.py` |
| The `Action` values and their sets | `src/provisioning/core/application/shared/action.py` |
| Permission, operation gate, project access | `src/provisioning/core/application/operations/gate.py` |
| Commands, queries, handlers | `src/provisioning/core/application/operations/commands/`, `queries/` |
| Handler registration | `src/provisioning/core/application/operations/registry.py` |
| Contracts | `src/contracts/openapi.yaml`, `src/contracts/mcp/tools.json` |
| MCP check of the tool file against the table | `src/provisioning/presentation/mcp/tools.py` |

Rules behind it: decisions `0006`, `0018` (Problem Details), `0023` (G1 to G14: envelope, camelCase, paging,
admin routes), `0025` (CQRS names). FR-1 to FR-8 apply to every request.

## The rules a new endpoint must keep

- **Success** is the envelope `{success, data, meta}` with `meta.path` the request path (`wire.envelope`). A list
  is `data` = items and `meta.page` = `{limit, nextCursor}`; on MCP a list is `{items, page}` (FR-46).
- **Names** a client sees are camelCase (`releaseId`); enum values and failure codes go out unchanged.
  Optional fields are left out when absent, lists are never null.
- **Paging**: `limit` 1 to 50, default 20, and an opaque `cursor` made only by `paging.py` (FR-38). Any other
  query parameter, a repeated one, or a query on an operation without paging is `VALIDATION_FAILED` with field
  `query`, and the value is never echoed (FR-7).
- **Errors** are Problems (`application/problem+json`) with a fixed message per code, never request content.
  The status must be in the operation's declared set, or the answer becomes `INTERNAL_ERROR`.
- **Auth** (FR-1 to FR-5, III.7, III.8): no or bad token is 401 `UNAUTHENTICATED`; a refused action is 403
  `ACCESS_DENIED`; a project the caller may not see is 404 `PROJECT_NOT_FOUND`, byte for byte the same as an
  unknown project. Admin-only actions need the `ADMIN` role and skip the Project access check.
- **Mutations** take no body (absent, empty, or `{}`), an `Idempotency-Key` header for a command, and answer
  202. Reads take no body and answer 200. A wrong method or unknown path is 404 `RESOURCE_NOT_FOUND`.
- **Production profile** refuses every mutation with 503 `OPERATION_UNAVAILABLE` (FR-6); reads are served.

## Steps

Each change cites its FR ID; with no FR, stop and go to stage 1 (`.claude/WORKFLOW.md`).

1. **Contract first.** Add the path and operation to `src/contracts/openapi.yaml` (camelCase parameters, the
   shared `components` parameters, responses and schemas, the Problem responses of every declared status).
   Add the tool to `src/contracts/mcp/tools.json`: `name` = the operation ID, `inputSchema` with exactly the
   operation's inputs (all strings but `limit`), `required` = the inputs that are not optional,
   `additionalProperties: false`. The owner approves a contract change (CLAUDE.md section 6).
2. **Tests first, with the FR ID** (`test_fr37_...` or `"""FR-37: ..."""`), and see them fail:
   - handler: `tests/unit/core/application/operations/` (`test_gate.py`, `test_reads.py`, `test_bus_surface.py`);
   - operation table and dispatch: `tests/unit/presentation/operations/test_operations.py`, `test_invoke.py`;
   - wire body: `tests/unit/presentation/operations/test_wire.py` (or `test_listing_wire.py` for a list);
   - routes, auth and Problems: `tests/unit/presentation/http/test_app.py`, `test_problems.py`, and
     `test_admin_routes.py` for an admin route;
   - contract conformance: `tests/contracts/test_http_conformance.py`, `test_mcp_conformance.py`,
     `test_wire_names.py` (every success validates against the schema, every name is camelCase).
   Use the fakes in `tests/unit/presentation/fakes.py` and the apps in `tests/unit/presentation/apps.py`.
   Cover the 401, 403, 404 concealment and 400 field cases, not only success (`.claude/rules/testing.md`).
3. **The Action value.** Add it to `Action` in `core/application/shared/action.py`, in contract order; its value
   is the operation ID and the MCP tool name. Put it in the right sets: `_COMMAND_KIND_OF` or `_MUTATIONS` for a
   mutation, `_ADMIN_ONLY` and `_ADMIN_ON_ANY_PROJECT` for an admin action. Every action needs a scope in
   `AUTH_ACTION_SCOPES` (`grep -rn AUTH_ACTION_SCOPES scripts .claude/docs/runbooks`); the config refuses a
   missing one. A mutation that needs an open contract goes into `PRODUCTION_REFUSED` in
   `hosts/shared/open_contracts.py`.
4. **The Operation entry.** Add it to `OPERATIONS` in `presentation/operations/operations.py`: method, path
   template, inputs in parse order (path IDs, then `idempotencyKey`, then `limit` and `cursor`), the status set
   (`_MUTATION` or `_READ`), the optional page inputs, and `not_found` (the code a missing row answers).
5. **The command or query and its handler.** One module per intent under
   `core/application/operations/commands/` or `queries/` (`.claude/rules/python-code.md`, "CQRS names"):
   `@dataclass(frozen=True, slots=True)` intent from `cqrs.Command[R]` or `cqrs.Query[R]`, and a handler whose only
   public method is `async def handle(self, intent, /)`. The handler calls `OperationGate.check` first, then the
   ports in `operations/ports.py`. Register it in `registry.py` (`OPERATION_COMMANDS` or `OPERATION_QUERIES` and
   `register_operations`) and re-export it from `core/application/__init__.py`.
6. **The dispatch case.** Add a `case Action.<NAME>:` to `Invoker._dispatch` in `invoke.py`: build the intent from
   the parsed `_Args`, send it, check the result belongs to the requested project (raise `_ResultMismatch`
   otherwise), and return `(status, data, page, False)`. `_dispatch` sits at the complexity ceiling
   (`C90`, `max-complexity = 18`, decision `0026`): if `make py-lint` reports C901, split the dispatch in a task of
   its own first. Never raise the ceiling or add a `noqa`.
7. **The wire encoder.** Add a `<thing>_body` function in `wire.py` that lists every field by name in camelCase
   (never `asdict`), leaves out absent optionals, and uses `timestamp` for times. A list returns
   `(items, Page)` with the next cursor from `paging.py`.
8. **The error codes.** A new refusal is an application or domain error class mapped in `CLASSES` in
   `errors.py`, before any error it can be raised from; a new code goes into `Code` with its status and fixed
   message, and into the description of the operation's response in `openapi.yaml`. A new release or command
   failure code also goes into `.claude/docs/runbooks/failure-codes.md`. The status must be in the operation's set.
9. **Gates.** `make py-fmt-check py-lint py-typecheck py-layers py-test contracts-check`; then the
   `provisioning-gates` skill for the rest. A contract change also needs the owner and, for another service's
   view, the consumer (OD-25).

## Never

- Request or response Pydantic models on a route, `Depends`, `HTTPException`, or FastAPI's own 405 or 422.
- Workflow logic in `presentation/`; a Kubernetes call outside the worker; a query that imports a command.
- A field name in snake_case on the wire, an echoed input value, or a message that differs by project.
- A token, credential or customer content in a log line or Problem (III.7).
