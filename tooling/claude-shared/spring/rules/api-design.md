---
paths:
  - "**/*.java"
---

# REST API rules

## Contract
- Contract first: the operation exists in `contracts/openapi/<service>.vN.yaml` before it is coded, and the implementation matches it exactly (paths, status codes, fields, errors).
- Do not change a released endpoint's behavior in place; add a new version. Springdoc annotations stay consistent with the contract.

## Shape
- Plural nouns, version in path: `/api/v1/projects/{projectId}`. Verbs only for real actions: `POST /api/v1/payments/{id}/refund`.
- Status codes: 200, 201 (+ `Location`), 204, 400 validation, 401, 403, 404, 409 conflict, 422 business rule, 429, 500 only for real bugs.
- Dedicated DTOs (records preferred). Never expose JPA entities; no `Map<String,Object>` bodies.
- Lists are paginated (`page`, `size` max 100, stable `sort`) and return a page envelope.
- Retriable mutations (payments, creates) accept an `Idempotency-Key` header.

## Validation and errors
- Bean Validation (`@Valid`, `@NotBlank`, `@Size`, `@Email`, `@Pattern`) on every body and constrained parameter.
- One error format: RFC 7807 `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, plus `errors[]` for field errors. No stack traces, SQL or class names.

## Example
```java
public record CreateProjectRequest(@NotBlank @Size(max = 100) String name) {}

@PostMapping
ResponseEntity<ProjectResponse> create(@Valid @RequestBody CreateProjectRequest r) { ... }
```
