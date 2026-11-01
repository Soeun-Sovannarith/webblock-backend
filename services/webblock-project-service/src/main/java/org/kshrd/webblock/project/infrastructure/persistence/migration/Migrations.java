package org.kshrd.webblock.project.infrastructure.persistence.migration;

import java.util.List;
import java.util.Map;
import org.flywaydb.core.api.FlywayException;
import org.flywaydb.core.api.output.MigrateResult;
import org.kshrd.webblock.project.application.catalog.handler.CatalogFlyway;
import org.kshrd.webblock.project.common.infrastructure.config.JdbcUrls;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The production migration job (decision 0005): migrates the project database, then the catalog database,
 * with the two migrator logins only, and exits. The service itself never migrates in production. Run from the boot
 * jar or image without starting Spring (.claude/docs/runbooks/migrations.md). Passwords come from the environment only.
 */
public final class Migrations {

    private static final List<String> REQUIRED = List.of(
            "PROJECT_DB_URL",
            "PROJECT_DB_MIGRATOR_USER",
            "PROJECT_DB_MIGRATOR_PASSWORD",
            "CATALOG_DB_URL",
            "CATALOG_DB_MIGRATOR_USER",
            "CATALOG_DB_MIGRATOR_PASSWORD");

    private static final Logger log = LoggerFactory.getLogger(Migrations.class);

    private Migrations() {}

    public static void main(String[] args) {
        // Spring's logging setup does not run here and logback's own default is DEBUG, which would log every statement.
        ((ch.qos.logback.classic.Logger) LoggerFactory.getLogger(org.slf4j.Logger.ROOT_LOGGER_NAME))
                .setLevel(ch.qos.logback.classic.Level.INFO);
        System.exit(run(System.getenv()));
    }

    public static int run(Map<String, String> settings) {
        List<String> missing = REQUIRED.stream()
                .filter(key -> settings.get(key) == null || settings.get(key).isBlank())
                .toList();
        if (!missing.isEmpty()) {
            log.error("Migration job: missing settings {}", missing);
            return 2;
        }
        try {
            // Before any connection: a URL parameter would replace the migrations' logServerErrorDetail=false.
            JdbcUrls.refuseDriverPropertyOverrides("PROJECT_DB_URL", settings.get("PROJECT_DB_URL"));
            JdbcUrls.refuseDriverPropertyOverrides("CATALOG_DB_URL", settings.get("CATALOG_DB_URL"));
        } catch (IllegalStateException e) {
            log.error("Migration job: {}", e.getMessage());
            return 2;
        }
        try {
            MigrateResult project = ProjectFlyway.configure(
                            settings.get("PROJECT_DB_URL"),
                            settings.get("PROJECT_DB_MIGRATOR_USER"),
                            settings.get("PROJECT_DB_MIGRATOR_PASSWORD"))
                    .migrate();
            log.info("Project database migrated: {} migration(s) applied", project.migrationsExecuted);
            MigrateResult catalog = CatalogFlyway.configure(
                            settings.get("CATALOG_DB_URL"),
                            settings.get("CATALOG_DB_MIGRATOR_USER"),
                            settings.get("CATALOG_DB_MIGRATOR_PASSWORD"))
                    .migrate();
            log.info("Catalog database migrated: {} migration(s) applied", catalog.migrationsExecuted);
            return 0;
        } catch (FlywayException e) {
            log.error("Migration job failed: {}", e.getMessage());
            return 1;
        }
    }
}
