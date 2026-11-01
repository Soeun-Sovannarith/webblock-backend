package org.kshrd.webblock.project.application.catalog.job;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@ServiceIntegrationTest
class CatalogRegistrationEndToEndTest {

    @Autowired
    CatalogRegistrationScheduler registrations;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate transaction;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JsonMapper json;

    @Autowired
    Environment environment;

    private Connection catalogAsOwner() throws Exception {
        Connection c = DriverManager.getConnection(
                environment.getProperty("webblock.catalog.datasource.url"),
                PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                PostgresFixture.PASSWORD);
        c.createStatement().execute("SET ROLE catalog_owner");
        return c;
    }

    @Test
    void theCatalogGetsTheWebsiteAndNoProfileDataRemainsInTheProjectDatabase() throws Exception {
        UUID type = UUID.randomUUID();
        try (Connection owner = catalogAsOwner();
                PreparedStatement ps =
                        owner.prepareStatement("INSERT INTO catalog.business_types VALUES (?, ?, 'T', true, now())")) {
            ps.setObject(1, type);
            ps.setString(2, "job_" + type.toString().substring(0, 8) + "_shop");
            ps.executeUpdate();
        }
        UUID projectId = UUID.randomUUID();
        BusinessProfile profile = new BusinessProfile(
                "Job shop", "Private description", "+855 99 999 999", "private@example.test", "Street 1", "UTC");
        transaction.executeWithoutResult(status -> {
            projects.create(
                    new NewProject(projectId, UUID.randomUUID(), "Job", TestSubdomains.of(projectId), type, "0.1.0"));
            registrations.schedule(projectId, profile);
        });

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            try (Connection owner = catalogAsOwner();
                    PreparedStatement ps =
                            owner.prepareStatement("SELECT count(*) FROM catalog.websites WHERE website_id = ?")) {
                ps.setObject(1, projectId);
                try (ResultSet rs = ps.executeQuery()) {
                    rs.next();
                    assertThat(rs.getLong(1)).isEqualTo(1);
                }
            }
            assertThat(jdbc.queryForObject(
                            "SELECT status FROM project_messaging.outbox_record WHERE record_key = ?",
                            String.class,
                            "catalog:" + projectId))
                    .isEqualTo("COMPLETED");
        });

        assertThat(jdbc.queryForObject(
                        "SELECT count(*) FROM project_messaging.pending_catalog_registrations WHERE project_id = ?",
                        Long.class,
                        projectId))
                .isZero();
        String payload = jdbc.queryForObject(
                "SELECT payload FROM project_messaging.outbox_record WHERE record_key = ?",
                String.class,
                "catalog:" + projectId);
        JsonNode node = json.readTree(payload);
        assertThat(List.copyOf(node.propertyNames())).containsExactly("projectId");
        assertThat(payload)
                .doesNotContain("private@example.test", "+855", "Private description", "Street 1", "Job shop");
    }

    @Test
    void schedulingOutsideATransactionIsRefused() {
        assertThatThrownBy(() -> registrations.schedule(
                        UUID.randomUUID(), new BusinessProfile("X", null, null, null, null, "UTC")))
                .isInstanceOf(IllegalTransactionStateException.class);
    }
}
