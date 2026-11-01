# Contracts

Source of truth for how services talk to each other. Code follows contracts, not the other way around.

```
contracts/
├── README.md
├── CHANGELOG.md                       every change, who must react
├── openapi/                           one file per service API
│   ├── user.v1.yaml
│   ├── project.v1.yaml
│   ├── payment.v1.yaml
│   ├── notification.v1.yaml
│   ├── aicode.v1.yaml
│   └── provisioning.v1.yaml
└── events/
    ├── rabbitmq.asyncapi.yaml         exchanges, routing keys, queues, DLQs
    └── schemas/                       JSON Schema per event payload
        ├── user-registered.v1.json
        └── ...
```
(The files above are created by each owner; this repo ships the structure and the rules.)

## Rules
1. **Contract first**: change the spec in a PR before the code.
2. **Ownership**: the provider owns its files (CODEOWNERS); consumers must approve.
3. **Versioning**: `name.vN.ext`. A released version is immutable except for additive changes.
4. **Breaking** = remove/rename/retype a field, change meaning or status code, optional -> required, stricter validation. Breaking changes ship as a new `vN+1` file; both run until consumers migrate.
5. **Events**: add optional fields only; never reuse a field with a new meaning. Envelope: `eventId, eventType, occurredAt, version, correlationId, payload`.
6. **Errors**: RFC 7807 `Problem` schema shared by all REST contracts.
7. **Security**: every OpenAPI file declares the Keycloak bearer scheme; public endpoints are explicit.
8. **Log it**: every change gets a line in `CHANGELOG.md` with consumers to notify.
9. **Validate**: syntax is checked by a hook; CI runs Spectral and schema validation.

Use the `contract-change` skill and the `/contract-impact` and `contract-checker` tools in `.claude/`.
