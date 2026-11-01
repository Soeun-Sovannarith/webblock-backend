---
paths:
  - "**/presentation/rest/**"
  - "**/common/presentation/**"
  - "**/*Controller*.java"
  - "**/*ExceptionHandler*.java"
---
# REST: what every answer looks like (Notion section 2)

Used by the frontend (Next.js server), the mobile app and admin pages. JSON field names are **camelCase**.

## Important: this is newer than `api-contracts.md`
The Notion page is dated 27 October 2026. Where it differs from `api-contracts.md` (page numbers vs cursors, `ProblemDetail`
vs the envelope), the page is the target. **Existing code is not changed automatically.** For a NEW endpoint use this file.
For an existing endpoint, keep its shape unless the user asks to migrate it. If unsure, ask (see `docs/specs/open-questions.md` #11).
The controller never builds the envelope by hand: the shared response advice in `common/presentation` adds it.

## Success
```json
{
  "success": true,
  "data": { "projectId": "0192f4a8-...", "name": "Sokha Clothing", "status": "ACTIVE" },
  "meta": {
    "requestId": "0192f4a8-9d01-...", "traceId": "4bf92f35...", "timestamp": "2026-10-24T08:15:30.123Z",
    "path": "/api/project/v1/projects/0192f4a8-...", "status": 200, "page": null
  }
}
```
| Field | Meaning |
|---|---|
| `success` | always `true` on this shape; errors use the shape below |
| `data` | the thing asked for (object, or a list) |
| `meta.requestId` | ID of this request; it is in every log line; quote it in bug reports |
| `meta.traceId` | Tempo trace ID; only when tracing picked this request |
| `meta.timestamp` | server time, UTC, milliseconds |
| `meta.path` | the path called, without the query |
| `meta.status` | the HTTP status, repeated for clients that lose it |
| `meta.page` | paging info on lists, else `null` |

**Lists** put items in `data` and `{ "limit": 20, "nextCursor": "..." }` in `meta.page`. Cursors, not page numbers
(the list stays correct while new items arrive). `nextCursor` is `null` on the last page. Client sends it back as `?cursor=`.

## Failure: RFC 9457 `application/problem+json`
```json
{
  "type": "urn:webblock:problem:project-not-found",
  "title": "No project with this ID was found.",
  "status": 404,
  "instance": "/api/project/v1/projects/0192f4a8-...",
  "code": "PROJECT_NOT_FOUND",
  "requestId": "0192f4a8-9d01-...",
  "traceId": "4bf92f35...",
  "errors": []
}
```
- **`code` is the field client code checks.** Stable, never renamed. `type` is the code in lower case with dashes.
- `title` is a short readable sentence, the same text every time for this code.
- `errors` lists bad fields for validation: `{ "field": "pages[0].path", "code": "TOO_LONG", "message": "At most 80 characters." }`.
- **Never echo what the caller sent.** No rejected value, no stack trace, no SQL, no file path. The `requestId` finds details in the logs.
- Codes are `UPPER_SNAKE`, start with the thing and end with what happened: `PROJECT_NOT_FOUND`, `PAGE_LIMIT_REACHED`, `HOSTNAME_TAKEN`.
  Each service keeps its list in ONE enum (`common/domain/ErrorCode`).

## Which status for which error
| Kind | HTTP | gRPC | Example code |
|---|---|---|---|
| Bad input | 400 | `INVALID_ARGUMENT` | `VALIDATION_FAILED` |
| Not logged in | 401 | `UNAUTHENTICATED` | `UNAUTHENTICATED` |
| Logged in, action not allowed for anyone like you | 403 | `PERMISSION_DENIED` | `ACCESS_DENIED` |
| Not found **or not yours** | 404 | `NOT_FOUND` | `PROJECT_NOT_FOUND` |
| Already exists | 409 | `ALREADY_EXISTS` | `HOSTNAME_TAKEN` |
| Changed by someone else, reload and retry | 409 | `ABORTED` | `STALE_EDIT_REVISION` |
| A business rule says no | 422 | `FAILED_PRECONDITION` | `PAGE_LIMIT_REACHED` |
| Too many requests | 429 | `RESOURCE_EXHAUSTED` | `RATE_LIMITED` |
| Our bug | 500 | `INTERNAL` | `INTERNAL_ERROR` |
| A service we need is down | 503 | `UNAVAILABLE` | `DEPENDENCY_UNAVAILABLE` |
| It took too long | 504 | `DEADLINE_EXCEEDED` | `DEPENDENCY_TIMEOUT` |

**Not the owner = 404, not 403** (decided 27 Oct 2026, same as `security.md`). A signed-in user asking for a project, website or
portal that is not theirs gets the same 404 as one that does not exist. 403 `ACCESS_DENIED` is only for "this action is not
allowed for anyone like you" (an admin endpoint without `ADMIN`).

## Rules for controllers
1. Controllers return the plain response record (or `ResponseEntity` with a status). The envelope is added for you.
2. `201` + `Location` on create; `202` + the request/command ID for long jobs (generate, deploy); `204` for delete with no body.
3. Validate with Bean Validation at the controller (`@Valid`); the shared handler turns it into `VALIDATION_FAILED` + `errors[]`.
4. Retryable writes require an `Idempotency-Key` header; a repeat returns the first result.
5. Dates ISO-8601 UTC. Money as a string decimal + `currencyCode`.
6. Owner routes vs admin routes: `/api/<service>/v1/admin/**` needs `ADMIN` (403 otherwise).
7. Keep the service's OpenAPI (Swagger) correct: `servers = @Server(url = "/")`.

## Tests
`@WebMvcTest`: 200 shape (`success`, `data`, `meta.requestId`), 400 with `errors[]`, 401, 404 for another owner's resource
(assert the SAME body as a missing resource), 409 `STALE_EDIT_REVISION`, no echo of the bad input.
