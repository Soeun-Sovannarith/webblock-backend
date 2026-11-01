---
paths:
  - "contracts/**"
---

# Contract rules

## Source of truth
- `contracts/` rules between services; code follows contracts, never the reverse.
- Layout: `openapi/<service>.vN.yaml`, `events/rabbitmq.asyncapi.yaml`, `events/schemas/<event>.vN.json`.
- The provider owns its files (CODEOWNERS); every consumer reviews changes.

## Versioning
- Additive (new optional field, endpoint or event) is allowed on a released version.
- Breaking needs a NEW `vN+1` file, both versions served during migration: remove or rename a field, change a type or meaning, optional to required, change a status code, tighten validation.
- Events: add optional fields only; envelope is `eventId, eventType, occurredAt, version, correlationId, payload`.

## Content
- Every change is a row in `contracts/CHANGELOG.md` (date, file, change, additive or breaking, consumers to notify).
- Errors use RFC 7807 `Problem`, defined once and referenced with `$ref`.
- Every OpenAPI file declares the Keycloak bearer scheme; no `security: []` unless the endpoint is explicitly public.
- Examples are valid and fake (`user@example.test`), never real tokens or secrets.
- Validate after each edit (hook checks syntax; CI runs Spectral).

Example: adding optional `description` to a response is additive. Making `name` required is breaking, so create `project.v2.yaml`.
