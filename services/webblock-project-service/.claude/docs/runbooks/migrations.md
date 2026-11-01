# Database migrations

| Profile | Who migrates | Credentials the service holds |
| --- | --- | --- |
| `prototype` | the service at start-up (Spring's Flyway for the project database; `CatalogMigrations` for the catalog) | app logins and both migrator logins |
| `production` | **a separate job** before each rollout (decision 0005) | app logins only; `ProfileGuard` refuses to start with in-process migrations enabled or with a migrator password set |

## The production job

The same artefact as the service, another main class, and only the migrator credentials:

```sh
java -Dloader.main=org.kshrd.webblock.project.infrastructure.persistence.migration.Migrations \
  -cp web-block.jar org.springframework.boot.loader.launch.PropertiesLauncher
```

Environment: `PROJECT_DB_URL`, `PROJECT_DB_MIGRATOR_USER`, `PROJECT_DB_MIGRATOR_PASSWORD`, `CATALOG_DB_URL`,
`CATALOG_DB_MIGRATOR_USER`, `CATALOG_DB_MIGRATOR_PASSWORD` (from a Secret the service's Deployment does not mount). Exit
codes: 0 migrated (or nothing to do), 1 a migration failed (nothing after it ran; fix forward with a new migration), 2 a
setting is missing or a URL sets `logServerErrorDetail`, `socketTimeout` or `options` (any case; refused before
connecting, the log names the parameter, not the URL). The project database is migrated first, then the catalog. Both
are idempotent: run the job on every rollout, before the new service version starts. In the container image, the Job
overrides the entry point with the same main class.

The service in production then validates the project schema through Hibernate (`ddl-auto=validate`) and refuses to
start against a schema that does not match its entities.

## Rolling out V6

Project `V6` (FR-36) adds `projects.subdomain`, renames the repository columns and drops `project.domains`. A service
version older than `V6` cannot use the new schema, and a running one would hold locks `V6` must wait for. So the old
version stops before the job runs (TR-39). There is no way back to the old version after `V6`: fix forward.

`V6` goes out in the same rollout as Provisioning's routing by sub-domain, and both are proved on Kind first, in an
owner-given slot (`.claude/docs/runbooks/j1-kind-exit.md`). Each existing website gets the sub-domain `p-` plus the
first 12 characters of its project ID, so its address changes to `p-<12 characters>.<site domain>`.

Steps, all run by the owner:

1. Count the sub-domain collisions, read-only, on the project database:

   ```sql
   SELECT left(project_id::text, 12), count(*) FROM project.projects GROUP BY 1 HAVING count(*) > 1;
   ```

   It must return no row. Any row stops the rollout: the owner decides those projects' sub-domains first. `V6` runs
   the same check itself and stops with SQLSTATE `23505`, changing nothing.
2. Stop the old version: scale the Project Deployment to zero replicas and wait until no Project pod runs.
3. Take a backup of the project database now, so no save is lost on a restore, because `V6` has no way back:
   start a CNPG on-demand backup, or confirm that a recent scheduled one is complete, and write down its name.
   Wait until its status is `completed`. Do not go on without it.
4. Run the migration job (above). Exit code 0 means the project database is at `V7` and the catalog at `V7`.
5. Start the new version, with Provisioning's routing by sub-domain.

Check, as the migrator login: `SELECT version, success FROM project_ops.flyway_schema_history ORDER BY
installed_rank;` ends with version 7, every row is `t`, and the new version starts (Hibernate validates the
schema).

Check the backup before step 3: the owner reads its status in the cluster (for a CNPG backup, `kubectl get backup
<name>`) and sees the phase `completed`, with the backup name written in the rollout notes. If `V6` fails for a reason
the job cannot fix, restore from that backup, then fix forward.
