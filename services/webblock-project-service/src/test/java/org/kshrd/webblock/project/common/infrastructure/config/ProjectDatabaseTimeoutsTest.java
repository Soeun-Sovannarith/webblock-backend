package org.kshrd.webblock.project.common.infrastructure.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.time.Duration;
import java.util.UUID;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.kshrd.webblock.project.support.Users;
import org.postgresql.jdbc.PgConnection;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * The project database pool bounds every statement, lock wait and socket read, so a stuck lock or a half-open
 * connection cannot pin a request (and with it a pooled connection) without end.
 */
@ServiceIntegrationTest
class ProjectDatabaseTimeoutsTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Environment environment;

    @Autowired
    DataSource dataSource;

    /** The socket timeout is read from the pooled pgjdbc connection itself (milliseconds), not from the settings. */
    @Test
    void everyPooledConnectionHasTheTimeouts() throws Exception {
        assertThat(jdbc.queryForObject("SHOW statement_timeout", String.class)).isEqualTo("10s");
        assertThat(jdbc.queryForObject("SHOW lock_timeout", String.class)).isEqualTo("5s");
        try (Connection pooled = dataSource.getConnection()) {
            assertThat(pooled.unwrap(PgConnection.class).getNetworkTimeout()).isEqualTo(15_000);
        }
    }

    @Test
    void aLockWaitEndsWithASanitized503() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        jdbc.update(
                "INSERT INTO project.projects (project_id, owner_user_id, name, subdomain, business_type_id, "
                        + "component_catalog_version, status, created_at, updated_at) "
                        + "VALUES (?, ?, 'Locked shop', ?, ?, '0.1.0', 'draft', now(), now())",
                projectId,
                owner,
                TestSubdomains.of(projectId),
                UUID.randomUUID());
        try (Connection holder = DriverManager.getConnection(
                environment.getProperty("spring.datasource.url"),
                PostgresFixture.APP_LOGIN,
                PostgresFixture.PASSWORD)) {
            holder.setAutoCommit(false);
            try (PreparedStatement lock =
                    holder.prepareStatement("SELECT 1 FROM project.projects WHERE project_id = ? FOR UPDATE")) {
                lock.setObject(1, projectId);
                lock.executeQuery().close();
            }

            long started = System.nanoTime();
            MvcResult result = mvc.perform(
                            post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                    .andExpect(status().isServiceUnavailable())
                    .andExpect(jsonPath("$.code").value("DEPENDENCY_UNAVAILABLE"))
                    .andExpect(header().string("Retry-After", "5"))
                    .andReturn();
            Duration waited = Duration.ofNanos(System.nanoTime() - started);

            assertThat(waited).isBetween(Duration.ofSeconds(4), Duration.ofSeconds(12));
            // the type member's urn:webblock:problem: prefix is the service's own name, not a leaked lock message
            assertThat(result.getResponse().getContentAsString().replace("urn:webblock:problem:", ""))
                    .doesNotContain("lock")
                    .doesNotContain("Locked shop")
                    .doesNotContain("project.projects");
            holder.rollback();
        }
    }
}
