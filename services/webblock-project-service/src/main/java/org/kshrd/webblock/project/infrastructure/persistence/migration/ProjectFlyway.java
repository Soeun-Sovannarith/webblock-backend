package org.kshrd.webblock.project.infrastructure.persistence.migration;

import java.util.List;
import java.util.Map;
import org.flywaydb.core.Flyway;

/** The project database's Flyway configuration for the migration job; equal to application.yml's spring.flyway. */
public final class ProjectFlyway {

    public static final String LOCATION = "classpath:db/migration/project";
    public static final List<String> SCHEMAS = List.of("project_ops", "project", "project_messaging");
    public static final String DEFAULT_SCHEMA = "project_ops";
    public static final String INIT_SQL = "SET ROLE project_migrator";

    /**
     * Keeps the server's error detail out of exception messages: pgjdbc otherwise adds "Failing row contains (...)",
     * which is customer content, to what Flyway and the migration job log.
     */
    public static final Map<String, String> JDBC_PROPERTIES = Map.of("logServerErrorDetail", "false");

    private ProjectFlyway() {}

    public static Flyway configure(String url, String user, String password) {
        return Flyway.configure()
                .dataSource(url, user, password)
                .jdbcProperties(JDBC_PROPERTIES)
                .initSql(INIT_SQL)
                .schemas(SCHEMAS.toArray(String[]::new))
                .defaultSchema(DEFAULT_SCHEMA)
                .locations(LOCATION)
                .load();
    }
}
