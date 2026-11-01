package org.kshrd.webblock.project.infrastructure.persistence.generation;

import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractSchemaEntity;
import org.springframework.data.jpa.repository.JpaRepository;

public interface GenerationContractSchemaRepository
        extends JpaRepository<GenerationContractSchemaEntity, GenerationContractSchemaEntity.Key> {

    List<GenerationContractSchemaEntity> findByGenerationContractIdIn(Collection<UUID> generationContractIds);
}
