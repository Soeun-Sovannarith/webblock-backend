package org.kshrd.webblock.project.db;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.concurrent.atomic.AtomicInteger;
import org.flywaydb.core.Flyway;
import org.testcontainers.postgresql.PostgreSQLContainer;

/** A disposable PostgreSQL 18 with the project's roles; each call to freshDatabase() returns a migrated database. */
public final class PostgresFixture {

    public static final String MIGRATOR_LOGIN = "project_migrator_login";
    public static final String APP_LOGIN = "project_app_login";
    public static final String PASSWORD = "test-only-password";

    private static final AtomicInteger COUNTER = new AtomicInteger();
    private static PostgreSQLContainer container;

    private PostgresFixture() {}

    public record Database(String url) {
        public Connection asApp() throws SQLException {
            return DriverManager.getConnection(url, APP_LOGIN, PASSWORD);
        }

        public Connection asSuperuser() throws SQLException {
            return DriverManager.getConnection(url, container.getUsername(), container.getPassword());
        }
    }

    public static synchronized Database freshDatabase() throws Exception {
        return freshDatabase(true);
    }

    public static synchronized Database freshDatabase(boolean migrate) throws Exception {
        start();
        String name = "project_test_" + COUNTER.incrementAndGet();
        try (Connection admin = DriverManager.getConnection(
                        container.getJdbcUrl(), container.getUsername(), container.getPassword());
                Statement st = admin.createStatement()) {
            st.execute("CREATE DATABASE " + name + " OWNER project_migrator");
            st.execute("REVOKE ALL ON DATABASE " + name + " FROM PUBLIC");
            st.execute("GRANT CONNECT ON DATABASE " + name + " TO project_app");
        }
        String url = container.getJdbcUrl().replaceFirst("/[^/?]+(\\?|$)", "/" + name + "$1");
        if (migrate) {
            flyway(url, MIGRATOR_LOGIN, true).migrate();
        }
        return new Database(url);
    }

    public static Flyway flyway(String url, String user, boolean setRole) {
        var config = Flyway.configure()
                .dataSource(url, user, user.equals(container.getUsername()) ? container.getPassword() : PASSWORD)
                .schemas("project_ops", "project", "project_messaging")
                .defaultSchema("project_ops")
                .locations("classpath:db/migration/project");
        if (setRole) {
            config.initSql("SET ROLE project_migrator");
        }
        return config.load();
    }

    public static final String CATALOG_MIGRATOR_LOGIN = "catalog_migrator_login";
    public static final String CATALOG_PLATFORM_LOGIN = "catalog_platform_login";
    private static boolean catalogRoles;

    public record CatalogDatabase(String url) {
        public Connection asPlatform() throws SQLException {
            return DriverManager.getConnection(url, CATALOG_PLATFORM_LOGIN, PASSWORD);
        }

        /** The migrator login with SET ROLE catalog_owner: reference rows in tests. */
        public Connection asOwner() throws SQLException {
            Connection connection = DriverManager.getConnection(url, CATALOG_MIGRATOR_LOGIN, PASSWORD);
            try (Statement st = connection.createStatement()) {
                st.execute("SET ROLE catalog_owner");
            }
            return connection;
        }

        public Connection asSuperuser() throws SQLException {
            return DriverManager.getConnection(url, container.getUsername(), container.getPassword());
        }

        public Connection as(String login) throws SQLException {
            return DriverManager.getConnection(url, login, PASSWORD);
        }
    }

    public static synchronized CatalogDatabase freshCatalogDatabase(boolean migrate) throws Exception {
        start();
        ensureCatalogRoles();
        String name = "catalog_test_" + COUNTER.incrementAndGet();
        try (Connection admin = DriverManager.getConnection(
                        container.getJdbcUrl(), container.getUsername(), container.getPassword());
                Statement st = admin.createStatement()) {
            st.execute("CREATE DATABASE " + name + " OWNER catalog_owner");
        }
        String url = container.getJdbcUrl().replaceFirst("/[^/?]+(\\?|$)", "/" + name + "$1");
        if (migrate) {
            org.kshrd.webblock.project.application.catalog.handler.CatalogFlyway.configure(
                            url, CATALOG_MIGRATOR_LOGIN, PASSWORD)
                    .migrate();
        }
        return new CatalogDatabase(url);
    }

    /** A site's login, as Provisioning's access_setup creates it: LOGIN, member of catalog_site only. */
    public static synchronized void createSiteLogin(String role) throws Exception {
        try (Connection admin = DriverManager.getConnection(
                        container.getJdbcUrl(), container.getUsername(), container.getPassword());
                Statement st = admin.createStatement()) {
            st.execute("CREATE ROLE " + role + " LOGIN PASSWORD '" + PASSWORD + "' IN ROLE catalog_site");
        }
    }

    private static void ensureCatalogRoles() throws Exception {
        if (catalogRoles) {
            return;
        }
        try (Connection admin = DriverManager.getConnection(
                        container.getJdbcUrl(), container.getUsername(), container.getPassword());
                Statement st = admin.createStatement()) {
            st.execute(Files.readString(Path.of("scripts/db/catalog-bootstrap-roles.sql")));
            st.execute("CREATE ROLE " + CATALOG_MIGRATOR_LOGIN + " LOGIN PASSWORD '" + PASSWORD + "'");
            st.execute("GRANT catalog_owner TO " + CATALOG_MIGRATOR_LOGIN + " WITH INHERIT FALSE, SET TRUE");
            st.execute("CREATE ROLE " + CATALOG_PLATFORM_LOGIN + " LOGIN PASSWORD '" + PASSWORD + "'");
            st.execute("GRANT catalog_platform TO " + CATALOG_PLATFORM_LOGIN + " WITH INHERIT TRUE, SET FALSE");
        }
        catalogRoles = true;
    }

    public static String superuser() {
        return container.getUsername();
    }

    public static String superuserPassword() {
        return container.getPassword();
    }

    private static void start() throws Exception {
        if (container != null) {
            return;
        }
        // Cached Spring contexts keep their pools open; the default 100 connections ran out (J1-R21).
        container = new PostgreSQLContainer("postgres:18").withCommand("postgres", "-c", "max_connections=300");
        container.start();
        try (Connection admin = DriverManager.getConnection(
                        container.getJdbcUrl(), container.getUsername(), container.getPassword());
                Statement st = admin.createStatement()) {
            st.execute(Files.readString(Path.of("scripts/db/bootstrap-roles.sql")));
            st.execute("CREATE ROLE " + MIGRATOR_LOGIN + " LOGIN PASSWORD '" + PASSWORD + "' IN ROLE project_migrator");
            st.execute("CREATE ROLE " + APP_LOGIN + " LOGIN PASSWORD '" + PASSWORD + "' IN ROLE project_app");
        }
    }
}
