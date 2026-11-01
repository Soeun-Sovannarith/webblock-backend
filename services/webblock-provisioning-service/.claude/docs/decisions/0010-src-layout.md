# 0010: Service code under `src/` in Clean Architecture folders

Amended by `0025`: the folders inside `core/domain` (one per kind) and `core/application`
(commands, queries, background and ports per feature). Read `0025` first.

Status: **decided by the project owner and applied** (task MV, merge `56efbea`, before the Phase 3 validation). Amends
decision `0003` and the folder sketch in ARCHITECTURE.md section 3, which now shows this layout.

## 1. What the owner asked for

All service ("platform") code lives under one folder `src/`, organised in the layers the owner knows from Clean
Architecture in .NET, with lowercase Go folder names. Everything that is not service code stays at the top level as
supporting ("miscellaneous") folders. A README map says which is which.

## 2. Mapping

| Today | After the move | Layer |
| --- | --- | --- |
| `cmd/api`, `cmd/api/apiwiring` | `src/hosts/api`, `src/hosts/api/apiwiring` | Composition roots (like `Program.cs`) |
| `cmd/worker`, `cmd/worker/workerwiring` | `src/hosts/worker`, `src/hosts/worker/workerwiring` | Composition roots |
| `internal/domain` | `src/core/domain` | Domain: entities, state machine, rules |
| `internal/application` | `src/core/application` | Application: use cases and the ports they consume |
| `internal/adapters/postgres` (with `jobstore`, and later `intakestore`, `buildstore`) | `src/infrastructure/postgres` | Infrastructure: persistence |
| `internal/adapters/rabbitmq` | `src/infrastructure/rabbitmq` | Infrastructure: messaging |
| `internal/adapters/identity` | `src/infrastructure/identity` | Infrastructure: token verification |
| `internal/adapters/unconfigured` | `src/infrastructure/unconfigured` | Infrastructure: fail-closed stand-ins for open contracts |
| `internal/platform/config`, `internal/platform/telemetry` | `src/infrastructure/config`, `src/infrastructure/telemetry` | Infrastructure: cross-cutting |
| `internal/transport/http`, `mcp`, `ops`, `messaging` | `src/presentation/http`, `mcp`, `ops`, `messaging` | Presentation: REST, MCP, message intake |
| `internal/jobs` | `src/workers` | Background work: dispatcher, consumer, inbox processor, build sweeper |
| `api/` (OpenAPI, MCP tools, event schemas, `embed.go`) | `src/contracts/` | Published contracts |
| `migrations/` | `src/migrations/` | Database schema |
| Later: `internal/adapters/kubernetes`, `crossplane`, `catalog` | `src/infrastructure/kubernetes`, `crossplane`, `catalog` | Infrastructure |
| Later: `cmd/build-worker` | `src/hosts/build-worker` | Composition root |

Top level, unchanged (supporting): `tests/` (integration, contracts, messaging, later acceptance), `deploy/` (Kind,
Crossplane XRDs, Compositions and the composition function's own Go module, policy, RBAC, later Helm), `scripts/`,
`.claude/docs/`, `ai/`, `.claude/`, `Makefile`, `go.mod`, `README.md`.

Each package keeps its unit tests in its own `test/` subfolder (decision `0003` section 2), which moves with it. Each
store's generated sqlc code stays in its own `internal/generated`, so Go's `internal` rule still keeps it private to its
store.

## 3. Rules that follow

- The Go module root stays the repository root (`go.mod` and module path `provision-service` unchanged, OD-19). Import
  paths change from `provision-service/internal/...` and `provision-service/cmd/...` to `provision-service/src/...`.
- Dependency direction is unchanged and stated in Clean Architecture terms: `core/domain` imports nothing of ours;
  `core/application` imports only `core/domain`; `infrastructure`, `presentation` and `workers` depend inward; only
  `hosts` wires concrete types. A test pins this (imports from `core` to any outer layer fail it).
- Moving out of `internal/` removes Go's import restriction on the service packages. Nothing outside this module imports
  them, and the layer test above replaces the protection that matters. The generated query packages keep theirs.
- The move is mechanical: `git mv`, import rewrite, and the paths in `Makefile`, `scripts/coverage.sh` (core roots
  become `src/core` and `src/workers`), the sqlc configurations (schema path), the migration directory used by goose and
  the test harness, `go:embed` directives, `.golangci` configuration, the dependency-list and not-wired tests, the
  runbooks, `README.md`, `.claude/CLAUDE.md`, `.claude/AGENTS.md` and ARCHITECTURE.md section 3. No behaviour changes;
  every gate must pass with the same test counts before and after.
- Decision records and validation reports written before the move keep their old paths as history; this record is the
  mapping.
