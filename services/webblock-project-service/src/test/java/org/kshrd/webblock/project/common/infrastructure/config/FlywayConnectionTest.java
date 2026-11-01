package org.kshrd.webblock.project.common.infrastructure.config;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;

/** The prototype's in-process project migration, like the pool and the job, never reads PostgreSQL's error detail. */
@ServiceIntegrationTest
class FlywayConnectionTest {

    /** Invalid JSON: PostgreSQL's error carries a DETAIL line ("The input string ended unexpectedly."). */
    private static final String FAILS_WITH_DETAIL = "SELECT '{\"a\":'::jsonb";

    @Autowired
    Flyway flyway;

    @Autowired
    Environment environment;

    @Test
    void theMigrationConnectionLeavesOutTheServerErrorDetail() throws Exception {
        try (Connection plain = DriverManager.getConnection(
                environment.getProperty("spring.flyway.url"),
                PostgresFixture.MIGRATOR_LOGIN,
                PostgresFixture.PASSWORD)) {
            assertThatThrownBy(() -> plain.createStatement().execute(FAILS_WITH_DETAIL))
                    .as("without the setting the driver puts the detail into the message")
                    .isInstanceOf(SQLException.class)
                    .hasMessageContaining("Detail");
        }
        try (Connection migration = flyway.getConfiguration().getDataSource().getConnection()) {
            assertThatThrownBy(() -> migration.createStatement().execute(FAILS_WITH_DETAIL))
                    .isInstanceOf(SQLException.class)
                    .hasMessageContaining("invalid input syntax for type json")
                    .hasMessageNotContaining("Detail");
        }
    }
}
