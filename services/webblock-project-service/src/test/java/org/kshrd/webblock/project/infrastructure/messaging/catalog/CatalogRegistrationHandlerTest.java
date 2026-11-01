package org.kshrd.webblock.project.infrastructure.messaging.catalog;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowable;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.job.CatalogRegistrationJob;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.catalog.model.OpeningRange;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.kshrd.webblock.project.infrastructure.config.catalog.CatalogProperties;
import org.springframework.core.NestedExceptionUtils;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.support.JdbcTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@Tag("integration")
class CatalogRegistrationHandlerTest {

    static final JsonMapper JSON = JsonMapper.builder().build();
    static final UUID TYPE = UUID.randomUUID();
    static PostgresFixture.CatalogDatabase catalogDb;
    static JdbcClient projectDb;
    static JdbcClient catalog;
    static TransactionTemplate catalogTx;

    final ProjectService projects = mock(ProjectService.class);

    @BeforeAll
    static void databases() throws Exception {
        catalogDb = PostgresFixture.freshCatalogDatabase(true);
        try (Connection owner = catalogDb.asOwner();
                PreparedStatement ps =
                        owner.prepareStatement("INSERT INTO catalog.business_types VALUES (?, ?, 'T', true, now())")) {
            ps.setObject(1, TYPE);
            ps.setString(2, "reg_" + TYPE.toString().substring(0, 8) + "_shop");
            ps.executeUpdate();
        }
        DriverManagerDataSource platform = new DriverManagerDataSource(
                catalogDb.url(), PostgresFixture.CATALOG_PLATFORM_LOGIN, PostgresFixture.PASSWORD);
        catalog = JdbcClient.create(platform);
        catalogTx = new TransactionTemplate(new JdbcTransactionManager(platform));
        PostgresFixture.Database project = PostgresFixture.freshDatabase();
        DriverManagerDataSource app =
                new DriverManagerDataSource(project.url(), PostgresFixture.APP_LOGIN, PostgresFixture.PASSWORD);
        projectDb = JdbcClient.create(app);
    }

    private CatalogRegistrationHandler handler(UUID projectId, UUID businessTypeId) {
        Instant t = Instant.parse("2026-10-15T00:00:00Z");
        when(projects.find(projectId))
                .thenReturn(Optional.of(new ProjectRecord(
                        projectId,
                        UUID.randomUUID(),
                        "P",
                        "h-shop",
                        businessTypeId,
                        0,
                        "0.1.0",
                        ProjectStatus.draft,
                        "h-shop.sites.webblock.test",
                        t,
                        t,
                        null)));
        return new CatalogRegistrationHandler(
                projectDb,
                catalog,
                catalogTx,
                projects,
                new CatalogProperties(null, null, 104_857_600L, 2_097_152L),
                JSON);
    }

    private static void pending(UUID projectId, BusinessProfile profile) {
        pending(projectId, profile, OffsetDateTime.now(ZoneOffset.UTC));
    }

    private static void pending(UUID projectId, BusinessProfile profile, OffsetDateTime requestedAt) {
        projectDb
                .sql("INSERT INTO project_messaging.pending_catalog_registrations VALUES (?, ?::jsonb, ?)")
                .params(projectId, JSON.writeValueAsString(profile), requestedAt)
                .update();
    }

    /** A website written by something other than this project's registration, as the demo shop's tool does. */
    private static void foreignWebsite(UUID websiteId, String roleName) throws Exception {
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement website = c.prepareStatement("""
                        INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status,
                          storage_limit_bytes, max_image_bytes, storage_used_bytes, created_at, updated_at)
                        VALUES (?, ?, ?, 'active', 1, 1, 0, now() - interval '1 day', now())""");
                PreparedStatement profile = c.prepareStatement("""
                        INSERT INTO catalog.business_profiles (website_id, business_name, email, timezone, created_at,
                          updated_at) VALUES (?, 'Demo shop', 'demo@example.test', 'UTC', now(), now())""")) {
            website.setObject(1, websiteId);
            website.setObject(2, TYPE);
            website.setString(3, roleName);
            website.executeUpdate();
            profile.setObject(1, websiteId);
            profile.executeUpdate();
        }
    }

    private static long websites(UUID websiteId) throws Exception {
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement ps =
                        c.prepareStatement("SELECT count(*) FROM catalog.websites WHERE website_id = ?")) {
            ps.setObject(1, websiteId);
            try (var rs = ps.executeQuery()) {
                rs.next();
                return rs.getLong(1);
            }
        }
    }

    private static long pendingRows(UUID projectId) {
        return projectDb
                .sql("SELECT count(*) FROM project_messaging.pending_catalog_registrations WHERE project_id = ?")
                .param(projectId)
                .query(Long.class)
                .single();
    }

    private static String catalogRow(UUID projectId) throws Exception {
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement ps = c.prepareStatement("""
                        SELECT w.status || '|' || w.db_role_name || '|' || w.storage_limit_bytes || '|' || w.max_image_bytes
                               || '|' || p.business_name || '|' || p.email || '|' || p.opening_hours::text
                        FROM catalog.websites w JOIN catalog.business_profiles p USING (website_id)
                        WHERE w.website_id = ?""")) {
            ps.setObject(1, projectId);
            try (var rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    private static BusinessProfile profile(String name) {
        return new BusinessProfile(
                name, "Clothes", "+855 12 345 678", "shop@example.test", "Phnom Penh", "Asia/Phnom_Penh");
    }

    @Test
    @DisplayName("FR-10.10: the profile's ranges are written to opening_hours in the catalog's shape")
    void fr10_10_theRangesAreWrittenToTheCatalog() throws Exception {
        UUID projectId = UUID.randomUUID();
        pending(
                projectId,
                new BusinessProfile(
                        "Hours",
                        null,
                        null,
                        "shop@example.test",
                        null,
                        "UTC",
                        List.of(
                                new OpeningRange("MONDAY", "FRIDAY", "09:00", "18:00", false),
                                new OpeningRange("SUNDAY", "SUNDAY", "10:00", "12:00", false))));
        handler(projectId, TYPE).handle(new CatalogRegistrationJob(projectId), null);
        assertThat(JSON.readTree(openingHours(projectId))).isEqualTo(JSON.readTree("""
                        [{"from_day":"MONDAY","to_day":"FRIDAY","opens_at":"09:00","closes_at":"18:00",
                          "closes_next_day":false},
                         {"from_day":"SUNDAY","to_day":"SUNDAY","opens_at":"10:00","closes_at":"12:00",
                          "closes_next_day":false}]"""));
    }

    @Test
    @DisplayName("FR-10.10: a pending row written before opening hours existed registers with []")
    void fr10_10_aPendingRowWithoutTheFieldRegistersWithNone() throws Exception {
        UUID projectId = UUID.randomUUID();
        projectDb
                .sql("INSERT INTO project_messaging.pending_catalog_registrations VALUES (?, ?::jsonb, ?)")
                .params(
                        projectId,
                        "{\"businessName\":\"Old\",\"description\":null,\"phone\":null,"
                                + "\"email\":\"old@example.test\",\"address\":null,\"timezone\":\"UTC\"}",
                        OffsetDateTime.now(ZoneOffset.UTC))
                .update();
        handler(projectId, TYPE).handle(new CatalogRegistrationJob(projectId), null);
        assertThat(openingHours(projectId)).isEqualTo("[]");
    }

    private static String openingHours(UUID projectId) throws Exception {
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement ps = c.prepareStatement(
                        "SELECT opening_hours::text FROM catalog.business_profiles WHERE website_id = ?")) {
            ps.setObject(1, projectId);
            try (var rs = ps.executeQuery()) {
                rs.next();
                return rs.getString(1);
            }
        }
    }

    @Test
    void writesTheCatalogRowsThenDeletesThePendingProfile() throws Exception {
        UUID projectId = UUID.randomUUID();
        pending(projectId, profile("First"));
        handler(projectId, TYPE).handle(new CatalogRegistrationJob(projectId), null);
        assertThat(catalogRow(projectId))
                .isEqualTo("active|site_" + projectId.toString().replace("-", "")
                        + "|104857600|2097152|First|shop@example.test|[]");
        assertThat(pendingRows(projectId)).isZero();
    }

    @Test
    void aReplayAfterSuccessDoesNothing() throws Exception {
        UUID projectId = UUID.randomUUID();
        pending(projectId, profile("Once"));
        CatalogRegistrationHandler handler = handler(projectId, TYPE);
        handler.handle(new CatalogRegistrationJob(projectId), null);
        handler.handle(new CatalogRegistrationJob(projectId), null);
        assertThat(catalogRow(projectId)).contains("|Once|");
    }

    @Test
    void aCrashBetweenTheCatalogWriteAndTheDeleteIsRepairedByTheRetry() throws Exception {
        UUID projectId = UUID.randomUUID();
        OffsetDateTime requestedAt = OffsetDateTime.parse("2026-10-15T08:00:00.123456Z");
        pending(projectId, profile("Retry"), requestedAt);
        CatalogRegistrationHandler handler = handler(projectId, TYPE);
        handler.handle(new CatalogRegistrationJob(projectId), null);
        // As if the delete had not committed: the same pending row is still there.
        pending(projectId, profile("Retry"), requestedAt);
        handler.handle(new CatalogRegistrationJob(projectId), null);
        assertThat(pendingRows(projectId)).isZero();
        assertThat(catalogRow(projectId)).contains("|Retry|shop@example.test|");
        assertThat(websites(projectId)).isEqualTo(1);
    }

    /**
     * SEC-L-3: a website with the project's ID that this registration did not write (the demo shop, registered by
     * Provisioning's tool with the same role naming and business type) is never adopted. The job fails without personal
     * data, the existing website and its profile stay as they were, and the pending profile waits for an operator.
     */
    @Test
    void aWebsiteRegisteredOutsideTheProjectServiceIsNotAdopted() throws Exception {
        UUID projectId = UUID.randomUUID();
        foreignWebsite(projectId, CatalogRegistrationHandler.siteRoleName(projectId));
        pending(projectId, profile("Taker"));

        Throwable thrown =
                catchThrowable(() -> handler(projectId, TYPE).handle(new CatalogRegistrationJob(projectId), null));

        assertThat(thrown)
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("The catalog already holds website " + projectId
                        + ", which this project's registration did not write");
        assertThat(catalogRow(projectId)).contains("|Demo shop|").doesNotContain("Taker");
        assertThat(pendingRows(projectId)).isEqualTo(1);
    }

    /** SEC-L-3: the site role the project's website needs is held by another website; nothing is written. */
    @Test
    void aSiteRoleHeldByAnotherWebsiteFailsTheJob() throws Exception {
        UUID projectId = UUID.randomUUID();
        foreignWebsite(UUID.randomUUID(), CatalogRegistrationHandler.siteRoleName(projectId));
        pending(projectId, profile("Second"));

        Throwable thrown =
                catchThrowable(() -> handler(projectId, TYPE).handle(new CatalogRegistrationJob(projectId), null));

        assertThat(thrown)
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("The catalog role of website " + projectId + " belongs to another website");
        for (Throwable t = thrown; t != null; t = t.getCause()) {
            assertThat(t.toString()).doesNotContain("shop@example.test", "Second");
        }
        assertThat(websites(projectId)).isZero();
        assertThat(pendingRows(projectId)).isEqualTo(1);
    }

    @Test
    void aFailedCatalogWriteKeepsThePendingProfileForTheRetry() {
        UUID projectId = UUID.randomUUID();
        pending(projectId, profile("Kept"));
        CatalogRegistrationHandler handler = handler(projectId, UUID.randomUUID()); // no such business type
        assertThatThrownBy(() -> handler.handle(new CatalogRegistrationJob(projectId), null))
                .isInstanceOf(DataAccessException.class);
        assertThat(pendingRows(projectId)).isEqualTo(1);
    }

    @Test
    void aFailedProfileWriteRollsBackTheWebsiteAndCarriesNoProfileData() throws Exception {
        UUID projectId = UUID.randomUUID();
        // The website row is written first; the profile then fails NOT NULL on business_name.
        pending(
                projectId,
                new BusinessProfile(
                        null, "Secret text", "+855 77 888 999", "leak@example.test", "Secret street 9", "UTC"));
        CatalogRegistrationHandler handler = handler(projectId, TYPE);

        Throwable thrown = catchThrowable(() -> handler.handle(new CatalogRegistrationJob(projectId), null));

        assertThat(thrown).isInstanceOf(DataAccessException.class);
        assertThat(NestedExceptionUtils.getMostSpecificCause(thrown))
                .isInstanceOfSatisfying(
                        SQLException.class, e -> assertThat(e.getSQLState()).isEqualTo("23502"));
        for (Throwable t = thrown; t != null; t = t.getCause()) {
            assertThat(t.toString())
                    .doesNotContain("Secret text", "+855", "leak@example.test", "Secret street", "Failing row");
        }
        try (Connection c = catalogDb.asSuperuser();
                PreparedStatement ps =
                        c.prepareStatement("SELECT count(*) FROM catalog.websites WHERE website_id = ?")) {
            ps.setObject(1, projectId);
            try (var rs = ps.executeQuery()) {
                rs.next();
                assertThat(rs.getLong(1))
                        .as("the website insert is rolled back")
                        .isZero();
            }
        }
        assertThat(pendingRows(projectId)).isEqualTo(1);
    }

    /** A website conflict cannot heal by waiting: the record goes to FAILED at once, for an operator. */
    @Test
    void aWebsiteConflictIsNotRetriedButADatabaseFailureIs() throws Exception {
        UUID projectId = UUID.randomUUID();
        foreignWebsite(projectId, CatalogRegistrationHandler.siteRoleName(projectId));
        pending(projectId, profile("Taker"));
        CatalogRegistrationHandler handler = handler(projectId, TYPE);
        Throwable conflict = catchThrowable(() -> handler.handle(new CatalogRegistrationJob(projectId), null));

        assertThat(handler.getRetryPolicy().shouldRetry(conflict)).isFalse();
        assertThat(handler.getRetryPolicy().shouldRetry(new DataAccessResourceFailureException("down")))
                .isTrue();
    }

    @Test
    void theRetryBudgetIsSixteenExponentialRetriesOfAbout44Minutes() {
        OutboxRetryPolicy policy = handler(UUID.randomUUID(), TYPE).getRetryPolicy();

        assertThat(policy.maxRetries()).isEqualTo(16);
        assertThat(policy.nextDelay(1)).isEqualTo(Duration.ofSeconds(1));
        assertThat(policy.nextDelay(2)).isEqualTo(Duration.ofSeconds(2));
        assertThat(policy.nextDelay(9)).isEqualTo(Duration.ofSeconds(256));
        assertThat(policy.nextDelay(10)).isEqualTo(Duration.ofMinutes(5));
        assertThat(policy.nextDelay(16)).isEqualTo(Duration.ofMinutes(5));
        Duration total = Duration.ZERO;
        for (int retry = 1; retry <= policy.maxRetries(); retry++) {
            total = total.plus(policy.nextDelay(retry));
        }
        // 1 + 2 + ... + 256 s, then 7 × 300 s: 2611 s, about 44 minutes before the record is FAILED.
        assertThat(total).isEqualTo(Duration.ofSeconds(2611));
    }
}
