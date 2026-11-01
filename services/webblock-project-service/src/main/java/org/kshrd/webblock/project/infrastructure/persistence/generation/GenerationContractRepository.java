package org.kshrd.webblock.project.infrastructure.persistence.generation;

import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractEntity;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface GenerationContractRepository extends JpaRepository<GenerationContractEntity, UUID> {

    /** The project's highest version_number, or 0 before its first contract; read under the project row lock. */
    @Query("select coalesce(max(c.versionNumber), 0) from GenerationContractEntity c where c.projectId = :projectId")
    int maxVersion(@Param("projectId") UUID projectId);

    boolean existsByProjectIdAndGenerationContractId(UUID projectId, UUID generationContractId);

    @Query("""
            select c from GenerationContractEntity c where c.projectId = :projectId
            order by c.versionNumber desc""")
    List<GenerationContractEntity> firstPage(@Param("projectId") UUID projectId, Limit limit);

    @Query("""
            select c from GenerationContractEntity c where c.projectId = :projectId and c.versionNumber < :versionNumber
            order by c.versionNumber desc""")
    List<GenerationContractEntity> pageAfter(
            @Param("projectId") UUID projectId, @Param("versionNumber") int versionNumber, Limit limit);
}
