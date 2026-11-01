package org.kshrd.webblock.project.application.project.model;

import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;

/** A project in the public API (camelCase); never carries the owner's ID. In application: the wizard returns it too. */
public record ProjectView(
        UUID projectId,
        String name,
        ProjectStatus status,
        UUID businessTypeId,
        long editRevision,
        String componentCatalogVersion,
        String subdomain,
        String platformHostname,
        boolean readOnly,
        Instant createdAt,
        Instant updatedAt,
        String businessTypeCode,
        String businessTypeName,
        String thumbnailUrl,
        boolean hasUndeployedChanges,
        Long lastDeployedEditRevision) {

    /** Without the look-ups: the display fields and the revision are null, and the site counts as never deployed. */
    public static ProjectView of(ProjectRecord project) {
        return of(project, null, null, null, null);
    }

    /**
     * The display fields come from the catalog (type code and name) and the bundled templates (thumbnail).
     * {@code lastDeployedEditRevision} is the edit revision of the last published code, null when never deployed; a
     * never-deployed website has undeployed changes, and a deployed one has them when saved after that revision.
     */
    public static ProjectView of(
            ProjectRecord project,
            String businessTypeCode,
            String businessTypeName,
            String thumbnailUrl,
            Long lastDeployedEditRevision) {
        return new ProjectView(
                project.projectId(),
                project.name(),
                project.status(),
                project.businessTypeId(),
                project.editRevision(),
                project.componentCatalogVersion(),
                project.subdomain(),
                project.platformHostname(),
                project.readOnly(),
                project.createdAt(),
                project.updatedAt(),
                businessTypeCode,
                businessTypeName,
                thumbnailUrl,
                lastDeployedEditRevision == null || project.editRevision() > lastDeployedEditRevision,
                lastDeployedEditRevision);
    }
}
