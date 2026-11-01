package org.kshrd.webblock.project.application.project.model;

import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;

/** A project as other features see it. */
public record ProjectRecord(
        UUID projectId,
        UUID ownerUserId,
        String name,
        String subdomain,
        UUID businessTypeId,
        long editRevision,
        String componentCatalogVersion,
        ProjectStatus status,
        String platformHostname,
        Instant createdAt,
        Instant updatedAt,
        Instant deletedAt) {

    public boolean deleted() {
        return deletedAt != null;
    }

    /** For other features, which do not name ProjectStatus (it is in this feature's domain, not its application). */
    public boolean suspended() {
        return status == ProjectStatus.suspended;
    }

    /** Deleted or suspended: never changed (decision 0007); the owner reads only a suspended one. */
    public boolean readOnly() {
        return deleted() || suspended();
    }
}
