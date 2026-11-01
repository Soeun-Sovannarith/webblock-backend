package org.kshrd.webblock.project.application.catalog.handler;

import java.util.Map;
import org.flywaydb.core.Flyway;

/**
 * The catalog database's Flyway configuration, shared by the in-process runner and the tests. The migrator login's
 * session becomes catalog_owner (the migrations' guard refuses any other role); the history table lives in its own
 * schema because V1 creates schema catalog. Placeholder replacement is off: the SQL is applied exactly as written.
 */
public final class CatalogFlyway {

    public static final String LOCATION = "classpath:db/migration/catalog";
    public static final String HISTORY_SCHEMA = "catalog_flyway";

    /**
     * Keeps the server's error detail out of exception messages: pgjdbc otherwise adds "Failing row contains (...)",
     * which is customer content, to what Flyway and the migration job log.
     */
    private static final Map<String, String> JDBC_PROPERTIES = Map.of("logServerErrorDetail", "false");

    private CatalogFlyway() {}

    public static Flyway configure(String url, String user, String password) {
        return Flyway.configure()
                .dataSource(url, user, password)
                .jdbcProperties(JDBC_PROPERTIES)
                .initSql("SET ROLE catalog_owner")
                .schemas(HISTORY_SCHEMA)
                .defaultSchema(HISTORY_SCHEMA)
                .locations(LOCATION)
                .placeholderReplacement(false)
                .load();
    }
}
