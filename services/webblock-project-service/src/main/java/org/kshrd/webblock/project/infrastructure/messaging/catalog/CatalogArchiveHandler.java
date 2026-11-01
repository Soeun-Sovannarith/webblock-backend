package org.kshrd.webblock.project.infrastructure.messaging.catalog;

import io.namastack.outbox.handler.OutboxRecordMetadata;
import io.namastack.outbox.handler.OutboxTypedHandler;
import io.namastack.outbox.retry.OutboxRetryAware;
import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.job.CatalogArchiveJob;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Archives the catalog website of a deleted project as catalog_platform (Notion "6 · Shared catalog": deleting the
 * project archives it). Only {@code status} is written: updated_at is outside catalog_platform's column grant.
 *
 * <p>Idempotent: an already archived website is left alone, so a replay changes nothing. The website of a project
 * whose registration job has not run yet does not exist: the job then fails and is retried with the registration's
 * policy until the registration has written it (then it is archived) or is gone with no website (then there is
 * nothing to archive and the job completes). The pending registration is read before the update, so a registration
 * that finishes in between is never missed.
 *
 * <p>While a registration is pending, only the row that registration writes is archived (its business type, role name
 * and created_at, as {@code CatalogRegistrationHandler} defines it). A website under the project's ID that is not
 * that row, such as a demo shop, is never touched: the job fails at once without a retry, for an operator. A
 * registration that can never finish keeps its pending row, so this job ends FAILED after its retries, for an
 * operator (.claude/docs/runbooks/catalog-database.md). The website's rows, profile and history stay.
 */
@Component
public class CatalogArchiveHandler implements OutboxTypedHandler<CatalogArchiveJob>, OutboxRetryAware {

    private static final Logger log = LoggerFactory.getLogger(CatalogArchiveHandler.class);

    private static final String PENDING_REGISTRATION =
            "SELECT created_at FROM project_messaging.pending_catalog_registrations WHERE project_id = ?";
    private static final String ARCHIVE =
            "UPDATE catalog.websites SET status = 'archived' WHERE website_id = ? AND status <> 'archived'";
    private static final String ARCHIVE_OWN = """
            UPDATE catalog.websites SET status = 'archived'
            WHERE website_id = ? AND business_type_id = ? AND db_role_name = ? AND created_at = ?
              AND status <> 'archived'""";
    private static final String OWN_WEBSITE = """
            SELECT count(*) FROM catalog.websites
            WHERE website_id = ? AND business_type_id = ? AND db_role_name = ? AND created_at = ?""";
    private static final String WEBSITE_EXISTS = "SELECT count(*) FROM catalog.websites WHERE website_id = ?";

    /** The registration has not written the website yet; waiting fixes it. */
    static final class RegistrationPending extends IllegalStateException {

        RegistrationPending(String message) {
            super(message);
        }
    }

    /** The catalog holds a website under the project's ID that its registration did not write; never touched. */
    static final class ForeignWebsite extends IllegalStateException {

        ForeignWebsite(String message) {
            super(message);
        }
    }

    private final JdbcClient projectDb;
    private final JdbcClient catalog;
    private final TransactionTemplate catalogTransaction;
    private final ProjectService projects;

    CatalogArchiveHandler(
            JdbcClient projectDb,
            @Qualifier("catalog") JdbcClient catalog,
            @Qualifier("catalog") TransactionTemplate catalogTransaction,
            ProjectService projects) {
        this.projectDb = projectDb;
        this.catalog = catalog;
        this.catalogTransaction = catalogTransaction;
        this.projects = projects;
    }

    @Override
    public void handle(CatalogArchiveJob job, OutboxRecordMetadata metadata) {
        UUID projectId = job.projectId();
        Optional<OffsetDateTime> requestedAt = projectDb
                .sql(PENDING_REGISTRATION)
                .param(projectId)
                .query((rs, row) -> rs.getObject(1, OffsetDateTime.class))
                .optional();
        if (requestedAt.isEmpty()) {
            archiveRegisteredWebsite(projectId);
            return;
        }
        UUID businessTypeId = projects.find(projectId)
                .orElseThrow(() -> new IllegalStateException("A pending catalog registration has no project"))
                .businessTypeId();
        String role = CatalogRegistrationHandler.siteRoleName(projectId);
        Object[] own = {projectId, businessTypeId, role, requestedAt.get()};
        Boolean done = catalogTransaction.execute(status -> {
            if (catalog.sql(ARCHIVE_OWN).params(own).update() > 0) {
                log.info("Catalog website of project {} archived", projectId);
                return true;
            }
            if (catalog.sql(OWN_WEBSITE).params(own).query(Long.class).single() > 0) {
                return true; // already archived
            }
            if (catalog.sql(WEBSITE_EXISTS).param(projectId).query(Long.class).single() > 0) {
                throw new ForeignWebsite("The catalog holds website " + projectId
                        + ", which this project's registration did not write; it is not archived");
            }
            return false;
        });
        if (!Boolean.TRUE.equals(done)) {
            throw new RegistrationPending("The catalog website of project " + projectId + " is not registered yet");
        }
    }

    /** No pending registration: it completed (the website is its own), or the project never had one. */
    private void archiveRegisteredWebsite(UUID projectId) {
        boolean known = Boolean.TRUE.equals(catalogTransaction.execute(status -> {
            if (catalog.sql(ARCHIVE).param(projectId).update() > 0) {
                log.info("Catalog website of project {} archived", projectId);
                return true;
            }
            return catalog.sql(WEBSITE_EXISTS)
                            .param(projectId)
                            .query(Long.class)
                            .single()
                    > 0;
        }));
        if (!known) {
            log.warn("Project {} has no catalog website and no pending registration: nothing to archive", projectId);
        }
    }

    @Override
    public OutboxRetryPolicy getRetryPolicy() {
        return OutboxRetryPolicy.builder()
                .maxRetries(CatalogRegistrationHandler.MAX_RETRIES)
                .noRetryOn(ForeignWebsite.class)
                .exponentialBackoff(Duration.ofSeconds(1), 2.0, Duration.ofMinutes(5))
                .build();
    }
}
