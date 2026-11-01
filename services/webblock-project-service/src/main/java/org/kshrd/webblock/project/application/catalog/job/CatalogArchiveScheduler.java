package org.kshrd.webblock.project.application.catalog.job;

import io.namastack.outbox.Outbox;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/** Schedules the archive of a deleted website's catalog row in the caller's project-database transaction. */
@Service
public class CatalogArchiveScheduler {

    private final Outbox outbox;

    public CatalogArchiveScheduler(Outbox outbox) {
        this.outbox = outbox;
    }

    /**
     * Key {@code catalog-archive:<projectId>}, not the registration's key: a registration that fails for good must
     * not hold the archive back, so the archive job waits for a pending registration itself.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public void schedule(UUID projectId) {
        outbox.schedule(new CatalogArchiveJob(projectId), "catalog-archive:" + projectId);
    }
}
