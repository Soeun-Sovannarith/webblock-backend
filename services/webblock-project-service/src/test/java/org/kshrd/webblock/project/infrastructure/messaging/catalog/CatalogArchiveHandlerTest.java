package org.kshrd.webblock.project.infrastructure.messaging.catalog;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.job.CatalogArchiveJob;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.support.JdbcTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Tag("integration")
class CatalogArchiveHandlerTest {

    static final UUID TYPE = UUID.randomUUID();
    static PostgresFixture.CatalogDatabase catalogDb;
    static JdbcClient projectDb;
    static CatalogArchiveHandler handler;
    static final ProjectService PROJECTS = mock(ProjectService.class);

    @BeforeAll
    static void databases() throws Exception {
        catalogDb = PostgresFixture.freshCatalogDatabase(true);
        try (Connection owner = catalogDb.asOwner();
                PreparedStatement ps =
                        owner.prepareStatement("INSERT INTO catalog.business_types VALUES (?, ?, 'T', true, now())")) {
            ps.setObject(1, TYPE);
            ps.setString(2, "arc_" + TYPE.toString().substring(0, 8) + "_shop");
            ps.executeUpdate();
        }
        DriverManagerDataSource platform = new DriverManagerDataSource(
                catalogDb.url(), PostgresFixture.CATALOG_PLATFORM_LOGIN, PostgresFixture.PASSWORD);
        PostgresFixture.Database project = PostgresFixture.freshDatabase();
        projectDb = JdbcClient.create(
                new DriverManagerDataSource(project.url(), PostgresFixture.APP_LOGIN, PostgresFixture.PASSWORD));
        handler = new CatalogArchiveHandler(
                projectDb,
                JdbcClient.create(platform),
                new TransactionTemplate(new JdbcTransactionManager(platform)),
                PROJECTS);
    }

    private static void website(UUID websiteId, String status) throws Exception {
        website(
                websiteId,
                status,
                OffsetDateTime.now(ZoneOffset.UTC),
                CatalogRegistrationHandler.siteRoleName(websiteId));
    }

    private static void website(UUID websiteId, String status, OffsetDateTime createdAt, String role) throws Exception {
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement ps = c.prepareStatement("""
                        INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status,
                          storage_limit_bytes, max_image_bytes, storage_used_bytes, created_at, updated_at)
                        VALUES (?, ?, ?, ?::catalog.website_status, 1, 1, 0, ?, now())""")) {
            ps.setObject(1, websiteId);
            ps.setObject(2, TYPE);
            ps.setString(3, role);
            ps.setString(4, status);
            ps.setObject(5, createdAt);
            ps.executeUpdate();
        }
    }

    private static String status(UUID websiteId) throws Exception {
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement ps =
                        c.prepareStatement("SELECT status::text FROM catalog.websites WHERE website_id = ?")) {
            ps.setObject(1, websiteId);
            try (var rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    /** A pending registration of a project of business type TYPE; returns its created_at. */
    private static OffsetDateTime pending(UUID projectId) {
        OffsetDateTime requestedAt = OffsetDateTime.now(ZoneOffset.UTC);
        projectDb
                .sql("INSERT INTO project_messaging.pending_catalog_registrations VALUES (?, '{}'::jsonb, ?)")
                .params(projectId, requestedAt)
                .update();
        Instant t = requestedAt.toInstant();
        when(PROJECTS.find(projectId))
                .thenReturn(Optional.of(new ProjectRecord(
                        projectId,
                        UUID.randomUUID(),
                        "P",
                        "a-shop",
                        TYPE,
                        0,
                        "0.1.0",
                        ProjectStatus.draft,
                        "a-shop.sites.webblock.test",
                        t,
                        t,
                        t)));
        return requestedAt;
    }

    private static void archive(UUID projectId) {
        handler.handle(new CatalogArchiveJob(projectId), null);
    }

    @Test
    @DisplayName("FR-40.1: an active website is archived; a second run changes nothing")
    void fr40_1_archivesAnActiveWebsiteOnce() throws Exception {
        UUID id = UUID.randomUUID();
        website(id, "active");

        archive(id);
        assertThat(status(id)).isEqualTo("archived");
        archive(id);
        assertThat(status(id)).isEqualTo("archived");
    }

    @Test
    @DisplayName("FR-40.1: a suspended website is archived too")
    void fr40_1_archivesASuspendedWebsite() throws Exception {
        UUID id = UUID.randomUUID();
        website(id, "suspended");

        archive(id);

        assertThat(status(id)).isEqualTo("archived");
    }

    @Test
    @DisplayName("FR-40.2: no website yet and a registration pending: the job fails to be retried")
    void fr40_2_waitsForAPendingRegistration() throws Exception {
        UUID id = UUID.randomUUID();
        OffsetDateTime requestedAt = pending(id);

        assertThatThrownBy(() -> archive(id)).isInstanceOf(CatalogArchiveHandler.RegistrationPending.class);

        // the registration runs: its own row carries the pending row's created_at
        website(id, "active", requestedAt, CatalogRegistrationHandler.siteRoleName(id));
        archive(id);
        assertThat(status(id)).isEqualTo("archived");
    }

    @Test
    @DisplayName("FR-40.4: with a registration pending, a website it did not write is never archived")
    void fr40_4_aForeignWebsiteIsNeverArchived() throws Exception {
        UUID id = UUID.randomUUID();
        pending(id);
        website(
                id,
                "active",
                OffsetDateTime.now(ZoneOffset.UTC).minusDays(1),
                "site_demo_" + id.toString().substring(0, 8));

        assertThatThrownBy(() -> archive(id)).isInstanceOf(CatalogArchiveHandler.ForeignWebsite.class);

        assertThat(status(id)).isEqualTo("active");
        assertThat(handler.getRetryPolicy().shouldRetry(new CatalogArchiveHandler.ForeignWebsite("x")))
                .as("a foreign website is not retried")
                .isFalse();
    }

    @Test
    @DisplayName("FR-40.2: no website and no pending registration: nothing to archive, the job completes")
    void fr40_2_completesWhenThereIsNothingToArchive() throws Exception {
        UUID id = UUID.randomUUID();

        archive(id);

        assertThat(status(id)).isNull();
    }

    @Test
    @DisplayName("FR-40.3: the archive job retries like the registration, sixteen times with backoff")
    void fr40_3_retriesLikeTheRegistration() {
        OutboxRetryPolicy policy = handler.getRetryPolicy();

        assertThat(policy.maxRetries()).isEqualTo(CatalogRegistrationHandler.MAX_RETRIES);
    }
}
