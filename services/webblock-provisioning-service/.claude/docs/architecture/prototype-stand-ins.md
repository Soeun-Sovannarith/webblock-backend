# Prototype-only stand-ins

The one list of what the prototype runs that production does not. Each stand-in is confined to the prototype,
named as a stand-in in its code or file, and refused by the production profile (`PROVISIONING_PROFILE=production`,
TR-47; [production-profile.md](../runbooks/production-profile.md)). Only `hosts/shared/stand_ins.py` builds one
(import-linter contract `standins-only-through-hosts-stand-ins`).

| Stand-in | Where | Production replacement | Open item |
| --- | --- | --- | --- |
| Static development bearer tokens (`IDENTITY_MODE=dev-tokens`) | `infrastructure/standins/dev_tokens.py` | JWT against Keycloak's key set (`IDENTITY_MODE=jwt`, TR-26) | none |
| Project access that allows everything (`PROJECT_ACCESS_MODE=prototype-allow`) or reads a reloadable file (`prototype-file`, `PROJECT_ACCESS_FILE`) | `infrastructure/standins/access.py` | Project's access check (`PROJECT_ACCESS_MODE=project-service`, the only mode production allows; an `ADMIN` rollback skips it, decision `0019` section 7) | none |
| Entitlement that allows everything (`ENTITLEMENT_MODE=prototype-allow`) or reads a reloadable file (`prototype-file`, `ENTITLEMENT_FILE`); an in-memory Payment event record | `standins/access.py`, `standins/entitlement_file.py`, `standins/entitlement_record.py` | Payment's `GetEntitlement`, asked when each command runs | OD-04 |
| Repository locator file `repositories.json` | `standins/repositories.py`, `standins/repository_reload.py` | Project's `GetRepository` (TR-20) | OD-01 |
| gRPC modes on draft protos: `REPOSITORY_MODE=project-grpc`, `ENTITLEMENT_MODE` and `HOSTING_ENTITLEMENT_MODE=payment-grpc` | `infrastructure/project/grpc_*.py`, `infrastructure/payment/grpc_entitlement.py` | The same adapters once Project and Payment accept the protos | OD-01, OD-04 |
| The simulator: declares `project.events`, `payment.events` and Payment's usage queue and publishes the v1 draft events as Project and Payment would | `hosts/devtools/simulate.py`, `src/contracts/shared/` | Project's and Payment's agreed events and exchanges | OD-02, OD-04, OD-42, OD-52 |
| One broker user shared by the worker and the operator's `dlq` commands | `scripts/prototype/run-provisioning.sh` | A broker user per process | OD-54 |
| Local Jenkins in Docker, privileged BuildKit, local Git server | `deploy/prototype/jenkins`, `buildkit`, `git` | The platform's Jenkins, an isolated builder, Gitea | OD-37, OD-62 |
| Local TLS registry `webblock-proto-registry.test:5000` | `deploy/kind` | The private Gitea container registry with a pull Secret per site (TR-46, TR-52) | none |
| Provisioning database, RabbitMQ and catalog database as single containers | `scripts/prototype`, `deploy/kind/proto-catalog.sh` | Database `provisioning` in the shared CloudNativePG cluster `platform-db` (TR-11) and the platform's RabbitMQ | OD-63 |
| Catalog schema migrations and the `catalog_platform` tool | `deploy/prototype/catalog/`, `hosts/devtools/catalog_platform.py` | Project's catalog ownership | OD-46 |
| Hostnames `*.sites.webblock.test`, preview hostnames under it | configuration `SITE_DOMAIN` | The platform's site domain | OD-36 |
| Local CA `ClusterIssuer` `webblock-local-ca` issuing the platform wildcard | `deploy/prototype/cert-manager`, `deploy/prototype/gateway`, `deploy/kind/proto-certs.sh` | The wildcard certificate from a production CA (TR-45) | OD-58 |
| Offline page nginx in `webblock-offline` | `deploy/prototype/offline` | The owned offline page | OD-44 |
| Admission policies and the tenant network policy with Kind parameters (local registry prefix, `*.sites.webblock.test`, the catalog container's address) | `deploy/prototype/policy`, `deploy/kind/proto-policy.sh` | The same policies with production parameters (TR-35) | none |
| Loki and a Fluent Bit log shipper; audit events only as log lines | `deploy/prototype/loki`, `deploy/kind/proto-loki.sh` | The platform's Loki; an audit store if OD-14 decides one | OD-14, OD-65 |
| Kind cluster with Cilium and its Gateway API | `deploy/kind`, `deploy/prototype/versions.sh` | DOKS with the Istio Gateway API (TR-44) | OD-67 |
| `psycopg[binary]` at runtime | `pyproject.toml` | Pure `psycopg` with the image's `libpq` (decision `0013` section 9, S2) | before production |
