package org.kshrd.webblock.project.application.project.handler;

import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.kshrd.webblock.project.application.project.config.PlatformProperties;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectOwnership;
import org.kshrd.webblock.project.application.project.model.ProjectPage;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.project.model.SubdomainAvailability;
import org.kshrd.webblock.project.application.project.response.ProjectCounts;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.project.model.ProjectEntity;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.kshrd.webblock.project.domain.project.model.Subdomain;
import org.kshrd.webblock.project.infrastructure.persistence.project.ProjectRepository;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Projects and their sub-domain, which gives the site hostname {@code <subdomain>.<site domain>}; the only way other
 * features read or change a project.
 */
@Service
public class ProjectService {

    public static final int MAX_LIST_LIMIT = 100;
    public static final int MAX_SEARCH_LENGTH = 100;

    private final ProjectRepository projects;
    private final PlatformProperties platform;
    private final Clock clock;

    public ProjectService(ProjectRepository projects, PlatformProperties platform, Clock clock) {
        this.projects = projects;
        this.platform = platform;
        this.clock = clock;
    }

    /** The sub-domain rule (FR-13): one DNS label, not reserved; else 400 with field subdomain, VALUE_INVALID. */
    public void requireValidSubdomain(String subdomain) {
        if (!subdomainAllowed(subdomain)) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("subdomain", ErrorCode.VALUE_INVALID)));
        }
    }

    /** Whether any project holds the sub-domain, a deleted one included: a sub-domain is never reused. */
    @Transactional(readOnly = true)
    public boolean subdomainTaken(String subdomain) {
        return projects.existsBySubdomain(subdomain);
    }

    /**
     * The create wizard's check (FR-13): INVALID where create answers VALUE_INVALID, TAKEN where create answers
     * HOSTNAME_TAKEN, else AVAILABLE. A refused label is never looked up.
     */
    @Transactional(readOnly = true)
    public SubdomainAvailability checkSubdomain(String subdomain) {
        if (!subdomainAllowed(subdomain)) {
            return SubdomainAvailability.INVALID;
        }
        return subdomainTaken(subdomain) ? SubdomainAvailability.TAKEN : SubdomainAvailability.AVAILABLE;
    }

    private boolean subdomainAllowed(String subdomain) {
        return Subdomain.isAllowed(subdomain, platform.reservedSubdomains());
    }

    /** The sub-domain is checked here too; projects_subdomain_key decides between two creates that both found it free. */
    @Transactional(propagation = Propagation.MANDATORY)
    public ProjectRecord create(NewProject project) {
        requireValidSubdomain(project.subdomain());
        ProjectEntity saved = projects.saveAndFlush(ProjectEntity.draft(
                project.projectId(),
                project.ownerUserId(),
                project.name(),
                project.subdomain(),
                project.businessTypeId(),
                project.componentCatalogVersion(),
                now()));
        return toRecord(saved);
    }

    /**
     * The row whoever owns it, a deleted one included: for the system's own flows (create's repeat check, catalog
     * registration, admission, codegen input). Anything a user or the access check reaches goes through
     * {@link #getOwned}, {@link #lockOwned} or {@link #ownership}, which treat a deleted project as not found.
     */
    @Transactional(readOnly = true)
    public Optional<ProjectRecord> find(UUID projectId) {
        return projects.findById(projectId).map(this::toRecord);
    }

    /** The owner and suspended state alone; a deleted project is absent, like an unknown one. */
    @Transactional(readOnly = true)
    public Optional<ProjectOwnership> ownership(UUID projectId) {
        return projects.findById(projectId)
                .filter(e -> e.deletedAt() == null)
                .map(e -> new ProjectOwnership(e.ownerUserId(), e.status() == ProjectStatus.suspended));
    }

    /** The owner's live project; a stranger's, an unknown and a deleted one are all PROJECT_NOT_FOUND. */
    @Transactional(readOnly = true)
    public ProjectRecord getOwned(UUID projectId, UUID ownerUserId) {
        return projects.findById(projectId)
                .filter(e -> e.deletedAt() == null && e.ownerUserId().equals(ownerUserId))
                .map(this::toRecord)
                .orElseThrow(() -> new WebBlockException(ErrorCode.PROJECT_NOT_FOUND));
    }

    /**
     * The owner's live project with its row locked until the caller's transaction ends. A suspended project is
     * returned: the caller decides (readOnly() tells it). A deleted one is PROJECT_NOT_FOUND.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public ProjectRecord lockOwned(UUID projectId, UUID ownerUserId) {
        return projects.findForUpdate(projectId)
                .filter(entity ->
                        entity.deletedAt() == null && entity.ownerUserId().equals(ownerUserId))
                .map(this::toRecord)
                .orElseThrow(() -> new WebBlockException(ErrorCode.PROJECT_NOT_FOUND));
    }

    /**
     * For the system's own flows (generation admission): the project, whoever owns it, with its row locked until the
     * caller's transaction ends; empty when it does not exist or is deleted. A suspended one is returned.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public Optional<ProjectRecord> lockIfLive(UUID projectId) {
        return projects.findForUpdate(projectId)
                .filter(entity -> entity.deletedAt() == null)
                .map(this::toRecord);
    }

    private static ProjectStatus parse(String status) {
        try {
            return ProjectStatus.valueOf(status);
        } catch (IllegalArgumentException e) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("status", ErrorCode.VALUE_INVALID)));
        }
    }

    /** A case-insensitive "contains" LIKE pattern; backslash, % and _ in the text match themselves. */
    private static String likePattern(String search) {
        if (search.isEmpty()) {
            return "%";
        }
        String escaped = search.toLowerCase(Locale.ROOT)
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
        return "%" + escaped + "%";
    }

    /**
     * The owner's live websites, newest first. {@code q} (optional, trimmed) keeps names containing it, ignoring case;
     * {@code status} (optional: draft, active or suspended) keeps one status. A cursor is only a position: the client sends the same q and status
     * with it, and a cursor used with other filters just continues the other list from that position.
     */
    @Transactional(readOnly = true)
    public ProjectPage listOwned(UUID ownerUserId, int limit, String cursor, String q, String status) {
        if (limit < 1 || limit > MAX_LIST_LIMIT) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("limit", ErrorCode.FIELD_OUT_OF_RANGE)));
        }
        String search = q == null ? "" : q.strip();
        if (search.length() > MAX_SEARCH_LENGTH) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("q", ErrorCode.FIELD_OUT_OF_RANGE)));
        }
        String pattern = likePattern(search);
        Set<ProjectStatus> statuses = status == null ? EnumSet.allOf(ProjectStatus.class) : EnumSet.of(parse(status));
        Limit fetch = Limit.of(limit + 1);
        List<ProjectEntity> rows;
        if (cursor == null) {
            rows = projects.firstPage(ownerUserId, pattern, statuses, fetch);
        } else {
            ProjectCursor after = ProjectCursor.decode(cursor);
            rows = projects.pageAfter(ownerUserId, pattern, statuses, after.createdAt(), after.projectId(), fetch);
        }
        boolean more = rows.size() > limit;
        List<ProjectRecord> page = (more ? rows.subList(0, limit) : rows)
                .stream().map(this::toRecord).toList();
        String next = more
                ? new ProjectCursor(page.getLast().createdAt(), page.getLast().projectId()).encode()
                : null;
        return new ProjectPage(page, next);
    }

    /** The owner's live websites per status (FR-14.10); a status with none is 0. */
    @Transactional(readOnly = true)
    public ProjectCounts countOwned(UUID ownerUserId) {
        Map<ProjectStatus, Long> byStatus = new EnumMap<>(ProjectStatus.class);
        for (Object[] row : projects.countByStatus(ownerUserId)) {
            byStatus.put((ProjectStatus) row[0], (Long) row[1]);
        }
        long draft = byStatus.getOrDefault(ProjectStatus.draft, 0L);
        long active = byStatus.getOrDefault(ProjectStatus.active, 0L);
        long suspended = byStatus.getOrDefault(ProjectStatus.suspended, 0L);
        return new ProjectCounts(draft + active + suspended, draft, active, suspended);
    }

    /** One live website per name for an owner, ignoring case and outer spaces (FR-10.12); 409 PROJECT_NAME_TAKEN. */
    @Transactional(readOnly = true)
    public void requireNameFree(UUID ownerUserId, String name) {
        if (projects.liveNameExists(ownerUserId, name.strip())) {
            throw new WebBlockException(ErrorCode.PROJECT_NAME_TAKEN);
        }
    }

    /**
     * Revision 19's edit rule: moves edit_revision from expectedEditRevision to the next value and keeps the project
     * row locked until the caller's transaction ends.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public long advanceEditRevision(UUID projectId, UUID ownerUserId, long expectedEditRevision) {
        int updated = projects.advanceEditRevision(
                projectId, ownerUserId, expectedEditRevision, now(), ProjectStatus.suspended);
        if (updated == 1) {
            return expectedEditRevision + 1;
        }
        throw whyNotChanged(projectId, ownerUserId, ErrorCode.STALE_EDIT_REVISION);
    }

    /** A conditional update changed no row: not the caller's project, read-only, or else what the caller names. */
    private WebBlockException whyNotChanged(UUID projectId, UUID ownerUserId, ErrorCode otherwise) {
        return new WebBlockException(
                getOwned(projectId, ownerUserId).readOnly() ? ErrorCode.PROJECT_READ_ONLY : otherwise);
    }

    /** The hostname comes from the row itself: one query per read. */
    private ProjectRecord toRecord(ProjectEntity e) {
        return new ProjectRecord(
                e.getId(),
                e.ownerUserId(),
                e.name(),
                e.subdomain(),
                e.businessTypeId(),
                e.editRevision(),
                e.componentCatalogVersion(),
                e.status(),
                e.subdomain() + "." + platform.siteDomain(),
                e.createdAt(),
                e.updatedAt(),
                e.deletedAt());
    }

    private Instant now() {
        return clock.instant().truncatedTo(ChronoUnit.MICROS);
    }
}
