# Security model

## Layers
| Layer | Control |
|---|---|
| Edge | TLS, rate limiting, CORS allow-list, JWT validation at the gateway |
| Service | JWT validation again, role check (`@PreAuthorize`), ownership check, input validation |
| Data | Database per service, least-privilege DB user, Flyway-managed schema, no PII in logs |
| Messaging | Dedicated RabbitMQ user per service, schema-validated payloads, DLQs, no secrets in payloads |
| Config | config-server per environment, secrets from environment/secret store, nothing in Git |
| Supply chain | Reviewed dependency changes, gitleaks in CI, vulnerability scan in CI |

## Claude Code guardrails (this repo)
| Guardrail | Where | What it enforces |
|---|---|---|
| Default plan mode, bypass disabled | `settings.json` | No edits without an approved plan; no "dangerously skip permissions" |
| Deny rules | `settings.json` | No reading `.env`, keys, prod config; no curl/ssh/sudo; no force push |
| `block-dangerous-bash.sh` | hook | Blocks destructive, privileged, networking and infra-changing shell commands |
| `scope-guard.sh` | hook | Edits only inside the folder where the session started |
| `protect-files.sh` | hook | No edits to secrets, prod config, CI, MCP config, or Claude's own settings/hooks |
| `protect-migrations.sh` | hook | Committed Flyway migrations are immutable |
| `protect-contracts.sh` | hook (root) | Released contract edits need explicit approval |
| `secret-scan.sh` | hook | Flags hard-coded secrets right after each edit |

These are **guardrails, not a sandbox**: pattern matching can be bypassed. For stronger isolation run Claude Code in a devcontainer/VM with no production credentials and no network route to production. CI remains the real enforcement point (gitleaks, ArchUnit, Spectral, `flyway validate`, dependency scan, CODEOWNERS reviews).

## Handling a suspected leak
1. Rotate the credential immediately (Keycloak client secret, DB password, API key).
2. Remove it from the code; purge from Git history if it was committed.
3. Check access logs for misuse.
4. Record what happened and the prevention in an ADR or the incident notes.
