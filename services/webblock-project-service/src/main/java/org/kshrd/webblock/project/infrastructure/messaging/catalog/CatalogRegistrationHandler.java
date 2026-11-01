package org.kshrd.webblock.project.infrastructure.messaging.catalog;

import io.namastack.outbox.handler.OutboxRecordMetadata;
import io.namastack.outbox.handler.OutboxTypedHandler;
import io.namastack.outbox.retry.OutboxRetryAware;
import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.job.CatalogRegistrationJob;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.infrastructure.config.catalog.CatalogProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/**
 * Registers a website in the catalog as catalog_platform, then deletes its pending profile. A missing pending row
 * means the work is done (a replay). The storage limits are the configured defaults until Payment carries plan limits.
 *
 * <p>The website's created_at is the time its registration was requested (the pending row's), which marks the row as
 * this registration's own. A crash after the catalog write therefore repeats harmlessly: the website insert conflicts
 * on its own key, the existing row carries the same business type, role and request time, and the profile insert keeps
 * the first write. Any other existing row, such as a demo shop registered outside the Project service under the same
 * ID, or the site role held by another website, fails the job without writing and without a retry: the record is
 * FAILED at once and an operator decides (.claude/docs/runbooks/catalog-database.md).
 *
 * <p>Each step stands alone, with no project-database transaction around the catalog write, so no project connection
 * waits on the catalog, whose statements the catalog pool bounds (CatalogDataSourceConfig). Namastack 1.7.3 calls
 * handlers outside a transaction, so the delete commits just before Namastack marks the
 * record complete; a replay after a crash in between finds no row and completes.
 */
@Component
public class CatalogRegistrationHandler implements OutboxTypedHandler<CatalogRegistrationJob>, OutboxRetryAware {

    static final int MAX_RETRIES = 16;

    private static final Logger log = LoggerFactory.getLogger(CatalogRegistrationHandler.class);

    private static final String SELECT_PENDING = """
            SELECT business_profile::text, created_at
            FROM project_messaging.pending_catalog_registrations WHERE project_id = ?""";
    private static final String DELETE_PENDING =
            "DELETE FROM project_messaging.pending_catalog_registrations WHERE project_id = ?";
    private static final String INSERT_WEBSITE = """
            INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status, storage_limit_bytes,
                                          max_image_bytes, storage_used_bytes, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?, 0, ?, now())
            ON CONFLICT (website_id) DO NOTHING""";
    private static final String OWN_WEBSITE = """
            SELECT count(*) FROM catalog.websites
            WHERE website_id = ? AND business_type_id = ? AND db_role_name = ? AND created_at = ?""";
    // Its only unique key is website_id; a conflict target would need SELECT on business_profiles, which
    // catalog_platform does not have. Reached only once the website is known to be this registration's own.
    private static final String INSERT_PROFILE = """
            INSERT INTO catalog.business_profiles (website_id, business_name, description, phone, email, address,
                                                   timezone, opening_hours, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb, now(), now())
            ON CONFLICT DO NOTHING""";

    private record Pending(String profile, OffsetDateTime requestedAt) {}

    /** The catalog holds a website this registration did not write; waiting cannot fix it, so it is not retried. */
    static final class WebsiteConflict extends IllegalStateException {

        WebsiteConflict(String message) {
            super(message);
        }

        WebsiteConflict(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private final JdbcClient projectDb;
    private final JdbcClient catalog;
    private final TransactionTemplate catalogTransaction;
    private final ProjectService projects;
    private final CatalogProperties properties;
    private final JsonMapper json;

    CatalogRegistrationHandler(
            JdbcClient projectDb,
            @Qualifier("catalog") JdbcClient catalog,
            @Qualifier("catalog") TransactionTemplate catalogTransaction,
            ProjectService projects,
            CatalogProperties properties,
            JsonMapper json) {
        this.projectDb = projectDb;
        this.catalog = catalog;
        this.catalogTransaction = catalogTransaction;
        this.projects = projects;
        this.properties = properties;
        this.json = json;
    }

    /** Provisioning's naming (credential.py: "site_" + project_id.hex): 37 characters. */
    static String siteRoleName(UUID projectId) {
        return "site_" + projectId.toString().replace("-", "");
    }

    @Override
    public void handle(CatalogRegistrationJob job, OutboxRecordMetadata metadata) {
        UUID projectId = job.projectId();
        Optional<Pending> pending = projectDb
                .sql(SELECT_PENDING)
                .param(projectId)
                .query((rs, row) -> new Pending(rs.getString(1), rs.getObject(2, OffsetDateTime.class)))
                .optional();
        if (pending.isEmpty()) {
            log.info("Catalog registration of project {} was already done", projectId);
            return;
        }
        BusinessProfile profile = json.readValue(pending.get().profile(), BusinessProfile.class);
        OffsetDateTime requestedAt = pending.get().requestedAt();
        UUID businessTypeId = projects.find(projectId)
                .orElseThrow(() -> new IllegalStateException("A pending catalog registration has no project"))
                .businessTypeId();
        String role = siteRoleName(projectId);
        catalogTransaction.executeWithoutResult(status -> {
            if (insertWebsite(projectId, businessTypeId, role, requestedAt) == 0
                    && catalog.sql(OWN_WEBSITE)
                                    .params(projectId, businessTypeId, role, requestedAt)
                                    .query(Long.class)
                                    .single()
                            == 0) {
                throw new WebsiteConflict("The catalog already holds website " + projectId
                        + ", which this project's registration did not write");
            }
            catalog.sql(INSERT_PROFILE)
                    .params(
                            projectId,
                            profile.businessName(),
                            profile.description(),
                            profile.phone(),
                            profile.email(),
                            profile.address(),
                            profile.timezone(),
                            json.writeValueAsString(profile.openingHours()))
                    .update();
        });
        projectDb.sql(DELETE_PENDING).param(projectId).update();
        log.info("Catalog website registered for project {}", projectId);
    }

    /** Rows inserted: 1, or 0 when the website's ID exists; a site role held by another website fails the job. */
    private int insertWebsite(UUID projectId, UUID businessTypeId, String role, OffsetDateTime requestedAt) {
        try {
            return catalog.sql(INSERT_WEBSITE)
                    .params(
                            projectId,
                            businessTypeId,
                            role,
                            properties.storageLimitBytes(),
                            properties.maxImageBytes(),
                            requestedAt)
                    .update();
        } catch (DuplicateKeyException e) {
            throw new WebsiteConflict("The catalog role of website " + projectId + " belongs to another website", e);
        }
    }

    @Override
    public OutboxRetryPolicy getRetryPolicy() {
        return OutboxRetryPolicy.builder()
                .maxRetries(MAX_RETRIES)
                .noRetryOn(WebsiteConflict.class)
                .exponentialBackoff(Duration.ofSeconds(1), 2.0, Duration.ofMinutes(5))
                .build();
    }
}
