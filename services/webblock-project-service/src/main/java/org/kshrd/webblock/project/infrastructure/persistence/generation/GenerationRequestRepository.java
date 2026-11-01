package org.kshrd.webblock.project.infrastructure.persistence.generation;

import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface GenerationRequestRepository extends JpaRepository<GenerationRequestEntity, UUID> {

    /**
     * Spec 5.3.5: one conditional UPDATE from an expected status. Returns the rows changed: 0 when the request is no
     * longer in {@code from}, so concurrent movers never both win.
     */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update GenerationRequestEntity r set r.status = :to, r.failureCode = :failureCode, r.updatedAt = :now
            where r.generationRequestId = :id and r.status = :from""")
    int transition(
            @Param("id") UUID generationRequestId,
            @Param("from") GenerationRequestStatus from,
            @Param("to") GenerationRequestStatus to,
            @Param("failureCode") String failureCode,
            @Param("now") Instant now);

    /** The oldest requests in {@code status} created before {@code cutoff}, at most the limit. */
    @Query("""
            select r.generationRequestId from GenerationRequestEntity r
            where r.status = :status
              and r.createdAt < :cutoff
            order by r.createdAt, r.generationRequestId""")
    List<UUID> findStuckCaptured(
            @Param("status") GenerationRequestStatus status, @Param("cutoff") Instant cutoff, Limit limit);

    boolean existsByProjectIdAndStatus(UUID projectId, GenerationRequestStatus status);

    boolean existsByProjectIdAndStatusIn(UUID projectId, java.util.Collection<GenerationRequestStatus> statuses);

    Optional<GenerationRequestEntity> findByProjectIdAndIdempotencyKey(UUID projectId, String idempotencyKey);

    Optional<GenerationRequestEntity> findByProjectIdAndGenerationRequestId(UUID projectId, UUID generationRequestId);

    @Query("""
            select r from GenerationRequestEntity r where r.projectId = :projectId
            order by r.createdAt desc, r.generationRequestId desc""")
    List<GenerationRequestEntity> firstPage(@Param("projectId") UUID projectId, Limit limit);

    @Query("""
            select r from GenerationRequestEntity r where r.projectId = :projectId
              and (r.createdAt < :createdAt
                   or (r.createdAt = :createdAt and r.generationRequestId < :generationRequestId))
            order by r.createdAt desc, r.generationRequestId desc""")
    List<GenerationRequestEntity> pageAfter(
            @Param("projectId") UUID projectId,
            @Param("createdAt") Instant createdAt,
            @Param("generationRequestId") UUID generationRequestId,
            Limit limit);

    /** The project's requests in {@code published}, by the time they reached it (updated_at), newest first. */
    @Query("""
            select r from GenerationRequestEntity r where r.projectId = :projectId and r.status = :published
            order by r.updatedAt desc, r.generationRequestId desc""")
    List<GenerationRequestEntity> publishedFirstPage(
            @Param("projectId") UUID projectId, @Param("published") GenerationRequestStatus published, Limit limit);

    @Query("""
            select r from GenerationRequestEntity r where r.projectId = :projectId and r.status = :published
              and (r.updatedAt < :updatedAt
                   or (r.updatedAt = :updatedAt and r.generationRequestId < :generationRequestId))
            order by r.updatedAt desc, r.generationRequestId desc""")
    List<GenerationRequestEntity> publishedPageAfter(
            @Param("projectId") UUID projectId,
            @Param("published") GenerationRequestStatus published,
            @Param("updatedAt") Instant updatedAt,
            @Param("generationRequestId") UUID generationRequestId,
            Limit limit);

    /** True when a published request of the project reached published after the given one (newer, or tied later). */
    @Query("""
            select count(r) > 0 from GenerationRequestEntity r where r.projectId = :projectId
              and r.status = :published
              and (r.updatedAt > :updatedAt
                   or (r.updatedAt = :updatedAt and r.generationRequestId > :generationRequestId))""")
    boolean existsPublishedAfter(
            @Param("projectId") UUID projectId,
            @Param("published") GenerationRequestStatus published,
            @Param("updatedAt") Instant updatedAt,
            @Param("generationRequestId") UUID generationRequestId);

    /** A project and the highest edit revision any of its published requests captured. */
    interface DeployedRevision {
        UUID getProjectId();

        long getRevision();
    }

    /** One query for many projects: the edit revision last deployed (max captured, status published), per project. */
    @Query("""
            select r.projectId as projectId, max(r.capturedEditRevision) as revision
            from GenerationRequestEntity r
            where r.projectId in :projectIds and r.status = :published
            group by r.projectId""")
    List<DeployedRevision> deployedRevisions(
            @Param("projectIds") Collection<UUID> projectIds, @Param("published") GenerationRequestStatus published);
}
