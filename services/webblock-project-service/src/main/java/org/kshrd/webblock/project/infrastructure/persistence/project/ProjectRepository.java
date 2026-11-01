package org.kshrd.webblock.project.infrastructure.persistence.project;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.domain.project.model.ProjectEntity;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ProjectRepository extends JpaRepository<ProjectEntity, UUID> {

    /** Over every project, deleted ones included: a sub-domain is never reused (Notion "2 · Project"). */
    boolean existsBySubdomain(String subdomain);

    /** The row with SELECT ... FOR UPDATE: locked until the caller's transaction ends. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select p from ProjectEntity p where p.projectId = :projectId")
    Optional<ProjectEntity> findForUpdate(@Param("projectId") UUID projectId);

    /** Revision 19's save rule: one conditional UPDATE, which also locks the row until the transaction ends. */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update ProjectEntity p set p.editRevision = p.editRevision + 1, p.updatedAt = :now
            where p.projectId = :projectId and p.ownerUserId = :owner and p.editRevision = :expected
              and p.deletedAt is null and p.status <> :suspended""")
    int advanceEditRevision(
            @Param("projectId") UUID projectId,
            @Param("owner") UUID owner,
            @Param("expected") long expected,
            @Param("now") Instant now,
            @Param("suspended") ProjectStatus suspended);

    /** Sets deleted_at once, in one conditional UPDATE: 0 rows for a stranger's, an unknown or a deleted project. */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update ProjectEntity p set p.deletedAt = :now, p.updatedAt = :now
            where p.projectId = :projectId and p.ownerUserId = :owner and p.deletedAt is null""")
    int markDeleted(@Param("projectId") UUID projectId, @Param("owner") UUID owner, @Param("now") Instant now);

    /** The owner's live project moves from one status to another: 0 rows when it is not the owner's, deleted, or not in {@code from}. */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update ProjectEntity p set p.status = :to, p.updatedAt = :now
            where p.projectId = :projectId and p.ownerUserId = :owner and p.deletedAt is null
              and p.status in :from""")
    int changeStatus(
            @Param("projectId") UUID projectId,
            @Param("owner") UUID owner,
            @Param("from") Collection<ProjectStatus> from,
            @Param("to") ProjectStatus to,
            @Param("now") Instant now);

    /** The owner's live projects per status, in one grouped query; a status with none has no row. */
    @Query("""
            select p.status, count(p) from ProjectEntity p
            where p.ownerUserId = :owner and p.deletedAt is null group by p.status""")
    List<Object[]> countByStatus(@Param("owner") UUID owner);

    /** Whether the owner has a live project named like this (the caller passes the name trimmed). */
    @Query("""
            select count(p) > 0 from ProjectEntity p
            where p.ownerUserId = :owner and p.deletedAt is null and lower(trim(p.name)) = lower(:name)""")
    boolean liveNameExists(@Param("owner") UUID owner, @Param("name") String name);

    /**
     * The owner's live projects, newest first, whose lower-cased name matches {@code pattern} (a LIKE pattern with
     * backslash as the escape; "%" for no search) and whose status is one of {@code statuses} (every status for no
     * filter).
     */
    @Query("""
            select p from ProjectEntity p where p.ownerUserId = :owner and p.deletedAt is null
              and p.status in :statuses and lower(p.name) like :pattern escape '\\'
            order by p.createdAt desc, p.projectId desc""")
    List<ProjectEntity> firstPage(
            @Param("owner") UUID owner,
            @Param("pattern") String pattern,
            @Param("statuses") Collection<ProjectStatus> statuses,
            Limit limit);

    @Query("""
            select p from ProjectEntity p where p.ownerUserId = :owner and p.deletedAt is null
              and p.status in :statuses and lower(p.name) like :pattern escape '\\'
              and (p.createdAt < :createdAt or (p.createdAt = :createdAt and p.projectId < :projectId))
            order by p.createdAt desc, p.projectId desc""")
    List<ProjectEntity> pageAfter(
            @Param("owner") UUID owner,
            @Param("pattern") String pattern,
            @Param("statuses") Collection<ProjectStatus> statuses,
            @Param("createdAt") Instant createdAt,
            @Param("projectId") UUID projectId,
            Limit limit);
}
