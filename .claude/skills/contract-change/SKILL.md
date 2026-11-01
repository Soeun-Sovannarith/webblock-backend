---
name: contract-change
description: Use when an API or event contract must be added or changed in contracts/ (OpenAPI, AsyncAPI, JSON Schema). Classifies the change, edits the spec, updates the changelog and lists affected consumers.
---

# Change a contract

## Purpose
Make every API or event change visible, classified and agreed before code changes. `contracts/` is the source of truth; code follows it (`contracts/README.md`).

## When to use / when NOT to use
- Use to add or change an endpoint, a field, an error response, an event, a routing key or a queue.
- Do NOT use to implement the change in a service (the provider and consumer owners do that after the contract merges), or for internal refactors that do not alter the wire format.

## Inputs to confirm with the user
Provider service and file (`contracts/openapi/<service>.vN.yaml` or `contracts/events/...`), the exact change, who consumes it (`docs/service-map.md`), and if breaking: a migration plan and deadline. If breaking and no plan exists, stop and ask.

## Procedure
1. Read the current spec, `contracts/CHANGELOG.md` and `docs/service-map.md` (provider and consumers).
2. **Classify**
   - Additive: new endpoint, new optional field, new event, new optional query param. Allowed on the current version.
   - Breaking: remove, rename or retype a field; change meaning or status code; optional to required; stricter validation. Create a NEW file `...v2.yaml` (or `...v2.json`), keep v1 running until consumers migrate.
3. Edit only files of the provider service. Keep the Keycloak bearer security scheme and the shared RFC 7807 `Problem` schema (`$ref`, do not copy). Use fake example values.
4. Events: update `contracts/events/rabbitmq.asyncapi.yaml` (exchange, routing key, queue, DLQ) and the payload schema `contracts/events/schemas/<event>.vN.json`. Envelope fields: `eventId, eventType, occurredAt, version, correlationId, payload`. Add optional fields only.
5. Add a row to `contracts/CHANGELOG.md` (newest first).
6. List each consumer and write a 2-3 line note per owner: what to do, by when, which version to target.
7. Validate: the hook checks syntax after edits; run `spectral lint` only if it is installed, and say if it was not run.
8. Hand off, no service code: contract PR first, then provider, then consumers, then cleanup of the old version.

## Templates
Changelog row (format `YYYY-MM-DD | file | change | additive/breaking | consumers to notify | author`):
```
| 2026-11-02 | contracts/openapi/project.v1.yaml | add optional `description` to ProjectResponse | additive | notification-service | <author> |
```
Owner note:
```
To: <owner> (<service>)   Contract: contracts/openapi/project.v1.yaml (additive)
What: new optional field `description`. Action: ignore or map it. By: <date>.
```

## Checklist
- [ ] Classified with reason; breaking change has a new version file
- [ ] Security scheme and Problem schema present; examples are fake
- [ ] Events: asyncapi plus schema updated, only optional additions
- [ ] CHANGELOG row added; consumers listed with notes
- [ ] Syntax validated; Spectral result stated

## Common mistakes
Editing a released version in a breaking way; copying the Problem schema instead of `$ref`; endpoint with `security: []` that is not meant to be public; reusing a field with a new meaning; forgetting the DLQ; implementing code in this step.

## Definition of done
Spec merged-ready, CHANGELOG updated, impact list and owner notes written, validation reported.

## Related
Command `/contract-impact`; agent `contract-checker`; rules `contracts.md`, `cross-service-changes.md`; skill `microservice-integration`; `contracts/README.md`.
