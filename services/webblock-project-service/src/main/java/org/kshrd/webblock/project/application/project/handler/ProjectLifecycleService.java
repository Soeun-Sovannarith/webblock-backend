package org.kshrd.webblock.project.application.project.handler;

import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.job.CatalogArchiveScheduler;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.application.project.model.ProjectDeleted;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.kshrd.webblock.project.infrastructure.persistence.project.ProjectRepository;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * The owner deletes a website, or suspends and reactivates it (FR-38, FR-39). Only the owner of a live project may:
 * a stranger, an unknown project and a deleted one are all PROJECT_NOT_FOUND.
 */
@Service
public class ProjectLifecycleService {

    private final ProjectRepository projects;
    private final ProjectService projectService;
    private final GenerationRequestService requests;
    private final CatalogArchiveScheduler archives;
    private final ApplicationEventPublisher events;
    private final Clock clock;

    public ProjectLifecycleService(
            ProjectRepository projects,
            ProjectService projectService,
            GenerationRequestService requests,
            CatalogArchiveScheduler archives,
            ApplicationEventPublisher events,
            Clock clock) {
        this.projects = projects;
        this.projectService = projectService;
        this.requests = requests;
        this.archives = archives;
        this.events = events;
        this.clock = clock;
    }

    /**
     * Sets deleted_at once, and in the same transaction publishes {@code project.project_deleted.v1} and schedules the
     * catalog archive. Only a draft website (never deployed) is deleted, and not while a generation request is in
     * flight. The owner check comes first, so a stranger never learns the status; the project row stays locked, so a
     * capture cannot slip in between the checks and the update.
     *
     * @throws WebBlockException PROJECT_NOT_FOUND, PROJECT_NOT_DELETABLE or GENERATION_IN_PROGRESS
     */
    @Transactional
    public void delete(UUID projectId, UUID ownerUserId) {
        ProjectRecord project = projectService.lockOwned(projectId, ownerUserId);
        if (project.status() != ProjectStatus.draft) {
            throw new WebBlockException(ErrorCode.PROJECT_NOT_DELETABLE);
        }
        if (requests.hasRequestInFlight(projectId)) {
            throw new WebBlockException(ErrorCode.GENERATION_IN_PROGRESS);
        }
        Instant now = now();
        if (projects.markDeleted(projectId, ownerUserId, now) == 0) {
            throw new WebBlockException(ErrorCode.PROJECT_NOT_FOUND);
        }
        events.publishEvent(ProjectDeleted.of(projectId, now));
        archives.schedule(projectId);
    }

    /**
     * A draft or active website becomes suspended: read-only, no effect on hosting. Already suspended: unchanged.
     * It blocks new work only: a request already generating may finish.
     */
    @Transactional
    public ProjectRecord suspend(UUID projectId, UUID ownerUserId) {
        ProjectRecord project = projectService.lockOwned(projectId, ownerUserId);
        if (!project.suspended()) {
            move(projectId, ownerUserId, List.of(ProjectStatus.draft, ProjectStatus.active), ProjectStatus.suspended);
        }
        return projectService.getOwned(projectId, ownerUserId);
    }

    /**
     * A suspended website becomes active if any generation request of it was published, else draft. Not suspended:
     * unchanged.
     */
    @Transactional
    public ProjectRecord reactivate(UUID projectId, UUID ownerUserId) {
        ProjectRecord project = projectService.lockOwned(projectId, ownerUserId);
        if (project.suspended()) {
            ProjectStatus target = requests.hasPublishedRequest(projectId) ? ProjectStatus.active : ProjectStatus.draft;
            move(projectId, ownerUserId, List.of(ProjectStatus.suspended), target);
        }
        return projectService.getOwned(projectId, ownerUserId);
    }

    private void move(UUID projectId, UUID ownerUserId, List<ProjectStatus> from, ProjectStatus to) {
        if (projects.changeStatus(projectId, ownerUserId, from, to, now()) == 0) {
            throw new WebBlockException(ErrorCode.PROJECT_NOT_FOUND);
        }
    }

    private Instant now() {
        return clock.instant().truncatedTo(ChronoUnit.MICROS);
    }
}
