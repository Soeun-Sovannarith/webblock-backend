package org.kshrd.webblock.project.infrastructure.persistence.page;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.domain.page.model.PageEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface PageRepository extends JpaRepository<PageEntity, UUID> {

    @Query("""
            select p from PageEntity p where p.projectId = :projectId and p.deletedAt is null
            order by p.sortOrder, p.createdAt, p.pageId""")
    List<PageEntity> findLive(@Param("projectId") UUID projectId);

    @Query("select p from PageEntity p where p.projectId = :projectId and p.pageId = :pageId and p.deletedAt is null")
    Optional<PageEntity> findLive(@Param("projectId") UUID projectId, @Param("pageId") UUID pageId);

    /** A live home page's project and the style key the editor stored at root.props.templateId (null when absent). */
    interface HomeTemplateKey {
        UUID getProjectId();

        String getTemplateId();
    }

    /** One query for many projects: the live home page ("/") of each. */
    @Query(nativeQuery = true, value = """
                    select project_id as projectId, puck_tree #>> '{root,props,templateId}' as templateId
                    from project.pages
                    where project_id in (:projectIds) and path = '/' and deleted_at is null""")
    List<HomeTemplateKey> homeTemplateKeys(@Param("projectIds") Collection<UUID> projectIds);
}
