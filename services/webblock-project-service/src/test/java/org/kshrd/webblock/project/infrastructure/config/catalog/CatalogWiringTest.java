package org.kshrd.webblock.project.infrastructure.config.catalog;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowable;

import com.zaxxer.hikari.HikariDataSource;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.job.CatalogRegistrationJob;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.infrastructure.messaging.catalog.CatalogRegistrationHandler;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.postgresql.util.PSQLException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.health.contributor.HealthIndicator;
import org.springframework.boot.health.contributor.Status;
import org.springframework.core.NestedExceptionUtils;
import org.springframework.core.env.Environment;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionTemplate;

@ServiceIntegrationTest
class CatalogWiringTest {

    @Autowired
    DataSource dataSource;

    @Autowired
    @Qualifier("catalog")
    DataSource catalogDataSource;

    @Autowired
    @Qualifier("catalog")
    JdbcClient catalog;

    @Autowired
    @Qualifier("catalogDbHealthIndicator")
    HealthIndicator catalogHealth;

    @Autowired
    Environment environment;

    @Autowired
    CatalogRegistrationHandler handler;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate transaction;

    @Autowired
    JdbcTemplate jdbc;

    @Test
    void theDefaultDataSourceIsTheProjectDatabaseAndTheQualifiedOneTheCatalog() {
        assertThat(((HikariDataSource) dataSource).getJdbcUrl())
                .isEqualTo(environment.getProperty("spring.datasource.url"));
        assertThat(((HikariDataSource) catalogDataSource).getJdbcUrl())
                .isEqualTo(environment.getProperty("webblock.catalog.datasource.url"));
    }

    @Test
    void theApplicationMigratedTheCatalogAndReportsItReady() {
        assertThat(catalog.sql("SELECT count(*) FROM catalog.business_types")
                        .query(Long.class)
                        .single())
                .isNotNull();
        assertThat(catalogHealth.health().getStatus()).isEqualTo(Status.UP);
    }

    /**
     * Lead ruling: a failed catalog write's message reaches logs and the outbox's failure_reason, so pgjdbc must not
     * append the server's Detail (for example "Failing row contains (...)", which would carry the business profile).
     * The catalog tables already hide row data from catalog_platform (no SELECT, row-level security), so the server's
     * Detail here comes from RAISE: it proves the pool's driver setting, not the tables' grants.
     */
    @Test
    void theCatalogPoolKeepsTheServerDetailOutOfErrorMessages() throws Exception {
        try (Connection c = catalogDataSource.getConnection();
                Statement statement = c.createStatement()) {
            Throwable thrown = catchThrowable(() -> statement.execute("""
                    DO $$ BEGIN
                        RAISE EXCEPTION 'refused' USING DETAIL = 'Failing row contains (secret@example.test)';
                    END $$"""));

            assertThat(thrown).isInstanceOfSatisfying(PSQLException.class, e -> {
                assertThat(e.getServerErrorMessage().getDetail())
                        .isEqualTo("Failing row contains (secret@example.test)");
                assertThat(e.toString()).contains("refused").doesNotContain("Detail", "secret@example.test");
            });
        }
    }

    @Test
    void everyCatalogStatementIsBoundedAndKeepsTheServerDetailOut() throws Exception {
        assertThat(((HikariDataSource) catalogDataSource).getDataSourceProperties())
                .containsEntry("logServerErrorDetail", "false")
                .containsEntry("socketTimeout", "10");
        assertThat(statementTimeout(catalogDataSource)).isEqualTo("5s");
    }

    /**
     * ProfileGuard refuses a CATALOG_DB_URL with its own options= at start-up. Should one reach the pool anyway (here
     * one that turns the timeout off), the timeout set on each new connection still holds.
     */
    @Test
    void aCatalogUrlWithItsOwnOptionsKeepsTheStatementTimeout() throws Exception {
        String url = environment.getProperty("webblock.catalog.datasource.url");
        String withOptions = url + (url.contains("?") ? "&" : "?") + "options=-c%20statement_timeout%3D0";
        CatalogProperties properties = new CatalogProperties(
                new CatalogProperties.Datasource(
                        withOptions,
                        environment.getProperty("webblock.catalog.datasource.username"),
                        environment.getProperty("webblock.catalog.datasource.password"),
                        1),
                null,
                1,
                1);
        try (HikariDataSource pool = new CatalogDataSourceConfig().catalogDataSource(properties)) {
            assertThat(statementTimeout(pool)).isEqualTo("5s");
        }
    }

    private static String statementTimeout(DataSource pool) throws SQLException {
        try (Connection c = pool.getConnection();
                Statement st = c.createStatement();
                ResultSet rs = st.executeQuery("SHOW statement_timeout")) {
            rs.next();
            return rs.getString(1);
        }
    }

    /**
     * Review M1: a catalog that answers but blocks (here a table lock) must fail the registration within the statement
     * timeout, and the handler must not hold a project-database transaction while it waits.
     */
    @Test
    void aBlockedCatalogFailsTheRegistrationWithinTheBoundAndHoldsNoProjectTransaction() throws Exception {
        UUID type = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        try (Connection owner = catalogAsOwner();
                PreparedStatement ps =
                        owner.prepareStatement("INSERT INTO catalog.business_types VALUES (?, ?, 'T', true, now())")) {
            ps.setObject(1, type);
            ps.setString(2, "lock_" + type.toString().substring(0, 8) + "_shop");
            ps.executeUpdate(); // a valid type: without the lock the registration would succeed
        }
        transaction.executeWithoutResult(status -> projects.create(
                new NewProject(projectId, UUID.randomUUID(), "Locked", TestSubdomains.of(projectId), type, "0.1.0")));
        jdbc.update(
                "INSERT INTO project_messaging.pending_catalog_registrations VALUES (?, ?::jsonb, now())",
                projectId,
                "{\"businessName\":\"Locked shop\",\"timezone\":\"UTC\"}");

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try (Connection lock = catalogAsOwner();
                Connection projectAdmin =
                        new PostgresFixture.Database(environment.getProperty("spring.datasource.url")).asSuperuser()) {
            lock.setAutoCommit(false);
            try (Statement st = lock.createStatement()) {
                st.execute("LOCK TABLE catalog.websites IN ACCESS EXCLUSIVE MODE");
            }
            long started = System.nanoTime();
            Future<?> handled = executor.submit(() -> handler.handle(new CatalogRegistrationJob(projectId), null));
            Throwable thrown = null;
            try {
                Thread.sleep(2_000); // the handler is now waiting on the catalog lock
                try (ResultSet rs = projectAdmin.createStatement().executeQuery("""
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname = current_database() AND usename = 'project_app_login'
                          AND state LIKE 'idle in transaction%' AND now() - state_change > interval '1 second'""")) {
                    rs.next();
                    assertThat(rs.getLong(1))
                            .as("project sessions idle in transaction")
                            .isZero();
                }
                handled.get(15, TimeUnit.SECONDS);
            } catch (ExecutionException e) {
                thrown = e.getCause();
            } finally {
                lock.rollback();
            }
            Duration elapsed = Duration.ofNanos(System.nanoTime() - started);

            assertThat(thrown).isInstanceOf(DataAccessException.class);
            assertThat(NestedExceptionUtils.getMostSpecificCause(thrown))
                    .isInstanceOfSatisfying(
                            SQLException.class,
                            e -> assertThat(e.getSQLState())
                                    .as("statement_timeout cancels the statement")
                                    .isEqualTo("57014"));
            assertThat(elapsed).isBetween(Duration.ofSeconds(4), Duration.ofSeconds(9));
        } finally {
            executor.shutdownNow();
        }
        assertThat(jdbc.queryForObject(
                        "SELECT count(*) FROM project_messaging.pending_catalog_registrations WHERE project_id = ?",
                        Long.class,
                        projectId))
                .isEqualTo(1);
    }

    private Connection catalogAsOwner() throws SQLException {
        Connection c = DriverManager.getConnection(
                environment.getProperty("webblock.catalog.datasource.url"),
                PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                PostgresFixture.PASSWORD);
        try (Statement st = c.createStatement()) {
            st.execute("SET ROLE catalog_owner");
        }
        return c;
    }
}
