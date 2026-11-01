# Provisioning technology stack

What this service uses, with exact versions. Sources: `pyproject.toml` and `uv.lock` (Python packages),
`scripts/py/versions.sh`, `deploy/kind/versions.sh` and `deploy/image/versions.sh` (tools and images). The rules
behind the choices are TRD TR-8 to TR-10 and decision `0013`. Every dependency is at least 14 days old, open source,
and pinned; images are pinned by digest (TR-9). The first-party `webblock-commons` is exempt from the age and open
source rules (constitution II.2): an exact release, its wheel committed under `vendor/`, its hash in `uv.lock`.

## Language and runtime

| Item | Version |
| --- | --- |
| Python | 3.14 (`requires-python >=3.14,<3.15`); image base `python:3.14.7-slim-trixie` |
| uv (resolver, lock, build backend `uv_build`) | 0.12.18 |

## Runtime libraries (`[project] dependencies`)

| Library | Version | Used for |
| --- | --- | --- |
| fastapi | 0.142.2 | REST API |
| uvicorn | 0.54.0 | ASGI server |
| pydantic | 2.13.5 | Request, response and event models |
| mcp | 2.3.0 | MCP tools over Streamable HTTP |
| sqlalchemy[asyncio] | 2.1.3 | SQL Core (no ORM) |
| psycopg[binary] | 3.3.6 | PostgreSQL driver (SQLAlchemy pools the connections) |
| aio-pika | 10.1.0 | RabbitMQ, publisher confirms |
| grpcio, grpcio-status | 1.84.0 | gRPC clients for Project and Payment |
| protobuf | 7.36.2 | Generated contract code |
| httpx2 | 2.13.1 | HTTP calls with bounded timeouts (Jenkins, key set) |
| kubernetes-asyncio | 36.1.0 | Server-side apply from the worker and bootstrap |
| pyjwt[crypto] | 2.15.1 | Bearer token check |
| structlog | 26.1.0 | JSON logs |
| opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http | 1.45.1 | Traces over OTLP |
| opentelemetry-instrumentation-fastapi | 0.66b1 | Request spans |
| prometheus-client | 0.26.0 | `/metrics` |
| sentry-sdk | 2.70.0 | Sanitized error reports |
| webblock-commons[fastapi,grpc] | 0.1.0 | The token check and the headers of calls to our services (decision `0024`) |

`webblock-commons` is the wheel of its GitHub Release `v0.1.0`, committed as
`vendor/webblock_commons-0.1.0-py3-none-any.whl`, SHA-256
`e1c494ef409790e3b238460b8f887097647c584a7fbc4e77a3adb47308e4a4f3` (the same hash in `uv.lock`). Its own pins
equal ours. `make py-vuln` and `make py-age` check the registry packages only; the wheel passed its own repository's
gates before release.

## What it connects to

| System | How | Requirement |
| --- | --- | --- |
| Its PostgreSQL 18 database `provisioning` (shared CloudNativePG cluster `platform-db`) | psycopg; schema by goose v3.28.0 migrations | TR-10, TR-11 |
| RabbitMQ 4 | aio-pika; Notion's exchange and queue names, `.dlq` queues | TR-4, TR-22, TR-23 |
| Keycloak (realm `webblock`) | Key set over HTTPS via web-block-commons; RS256, audience `provisioning`, roles `USER` and `ADMIN` | TR-26, TR-48 |
| Project and Payment | gRPC from `web-block-contracts` (`CheckAccess`, `GetRepository`, `GetEntitlement`) | TR-20, TR-21 |
| Jenkins | HTTP with its own token; triggers and polls one build per release | TR-25 |
| Gitea container registry | Site images, private, one per site, pulled by digest with a pull Secret per site | TR-46, TR-52 |
| Kubernetes API | Worker and bootstrap only, server-side apply, field manager `webblock-provisioning-worker` | TR-6, TR-44 |

## Test and quality tools

| Tool | Version | Gate |
| --- | --- | --- |
| ruff | 0.16.8 | `py-fmt-check`, `py-lint` |
| mypy (`--strict`) | 2.3.1 | `py-typecheck` |
| import-linter | 2.15 | `py-layers` |
| pylint (duplicate-code check only), vulture | 4.1.2, 2.16 | `qa-sweep`, not a gate (decision `0026`) |
| pytest, pytest-asyncio, pytest-cov, pytest-timeout | 9.1.1, 1.4.0, 7.1.0, 2.4.0 | every test target, `py-cover` |
| testcontainers | 4.15.0 | `postgres:18` and `rabbitmq:4.3.6-management-alpine` for integration and messaging |
| osv-scanner | v2.6.0 | `py-vuln` (offline, on `uv.lock`) |
| grype | v0.119.0 | `image-scan` |
| protoc | v36.2 | `contracts-sync`, `py-test` |
| helm | v3.22.0 | `chart-check` |

## How it ships

| Piece | Where | Version |
| --- | --- | --- |
| Runtime image | `deploy/image/Dockerfile` (`deploy/image/build.sh`) | base `python:3.14.7-slim-trixie` by digest |
| Helm chart `provisioning` | `deploy/helm/provisioning` (four Deployments, migrate Job) | chart 0.1.0 |
| Local Kind cluster | `deploy/kind` (`make proto-up`) | kind v0.33.0, `kindest/node:v1.36.4`, kubectl v1.36.5 |

The rest of the local Kind stack (Cilium, Gateway API, Jenkins, BuildKit, registry, Loki) is pinned in
`deploy/prototype/versions.sh` and `deploy/kind/versions.sh`; it runs only on Kind (`.claude/docs/runbooks/`).
