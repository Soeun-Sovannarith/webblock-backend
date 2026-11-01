# Decisions

One file per binding decision: `NNNN-short-name.md` with three sections, **Decision**, **Reason** and **Status**
(Proposed, Accepted, or Superseded by `NNNN`). A decision is written once the owner approves it. To change one, write
a new record and mark the old one Superseded. Current facts only; git history is the record of changes.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-outbox-with-modulith-and-namastack.md) | Outbox with Spring Modulith and Namastack on RabbitMQ | Accepted |
| [0002](0002-authentication-and-service-identity.md) | Authentication and service identity: user JWT, mesh identity, no service tokens | Accepted |
| [0003](0003-http-and-event-formats.md) | HTTP and event formats: camelCase, snake_case events, envelope, Problem Details | Accepted |
| [0004](0004-generation-capture-and-admission.md) | Generation capture and admission | Accepted |
| [0005](0005-production-migrations-in-a-separate-job.md) | Production migrations in a separate job | Accepted |
| [0006](0006-codegen-owns-the-build.md) | AI Codegen owns the build; Project checks paths, limits and secrets | Accepted |
| [0007](0007-project-access-rules.md) | Project access rules: owner only, read-only after deletion | Accepted |
| [0008](0008-one-deployable.md) | One deployable | Accepted |
| [0009](0009-code-quality-tools.md) | Code quality tools: PMD, CPD, SpotBugs, javac lint; report-only | Accepted |
| [0010](0010-java-layout-notion-6-1.md) | Java layout of Notion 6.1: request, response and validation in `application/`, gRPC in `presentation/grpc` | Superseded by 0016 |
| [0011](0011-grpc-check-access-and-pinned-contracts.md) | gRPC CheckAccess on port 9090, error statuses, contracts pinned from `web-block-contracts`, one action list | Accepted |
| [0012](0012-checks-on-internal-tables.md) | Checks on the internal tables of `project_messaging` stay: inbox `outcome` shape, pending profile is a JSON object | Accepted |
| [0013](0013-grants-on-internal-tables.md) | `project_app` grants on the internal tables: SELECT, INSERT on the inbox; SELECT, INSERT, DELETE on pending catalog registrations | Accepted |
| [0014](0014-catalog-websites-updated-at.md) | `catalog.websites.updated_at` is set at registration only; a later change waits for an FR | Accepted |
| [0015](0015-catalog-database-public-connect.md) | The catalog database keeps PUBLIC CONNECT for the per-site login roles | Accepted |
| [0016](0016-layer-first-java-layout.md) | Layer-first Java layout of Notion 6.1; features are not Modulith modules; services stay until the handler split | Accepted |
| [0017](0017-subdomain-label-rule.md) | A sub-domain label is 3 to 55 characters, never ends with `-preview`, never starts with `xn--`; the reserved list is never empty in production | Accepted |

The database design (tables, columns, indexes, grants) is decided in the Notion platform-db-v6 pages, not here.
