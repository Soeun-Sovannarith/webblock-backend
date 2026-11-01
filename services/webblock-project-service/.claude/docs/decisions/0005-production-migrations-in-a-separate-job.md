# 0005 Production migrations in a separate job

## Decision

In production a separate job (`org.kshrd.webblock.project.infrastructure.persistence.migration.Migrations`) migrates
the project and catalog databases before each rollout. The service holds only app logins and refuses to start with
in-process migrations enabled or a migrator password set. The prototype profile migrates in-process at start-up.

No JDBC URL may set `logServerErrorDetail`, `socketTimeout` or `options`: pgjdbc would let them replace the pool's own
settings. The service refuses to start and the job exits 2 before connecting.

## Reason

The running service never holds a credential that can change the schema. Migrations run once per rollout, not once
per pod.

## Status

Accepted. Built. How to run it: `.claude/docs/runbooks/migrations.md`.
