package org.kshrd.webblock.project.domain.generation.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.io.Serializable;
import java.util.UUID;
import org.hibernate.annotations.Immutable;
import org.springframework.data.domain.Persistable;

/**
 * One product-type schema a contract froze. Only the schema's ID is kept here; its version and fields stay in the
 * catalog.
 */
@Entity
@Immutable
@IdClass(GenerationContractSchemaEntity.Key.class)
@Table(schema = "project", name = "generation_contract_schemas")
public class GenerationContractSchemaEntity implements Persistable<GenerationContractSchemaEntity.Key> {

    /** The composite primary key (generation_contract_id, product_type_schema_id). */
    public record Key(UUID generationContractId, UUID productTypeSchemaId) implements Serializable {}

    @Id
    @Column(name = "generation_contract_id", nullable = false, updatable = false)
    private UUID generationContractId;

    @Id
    @Column(name = "product_type_schema_id", nullable = false, updatable = false)
    private UUID productTypeSchemaId;

    @Column(name = "product_type_id", nullable = false, updatable = false)
    private UUID productTypeId;

    @Transient
    private boolean newEntity;

    protected GenerationContractSchemaEntity() {}

    public static GenerationContractSchemaEntity create(
            UUID generationContractId, UUID productTypeSchemaId, UUID productTypeId) {
        GenerationContractSchemaEntity entity = new GenerationContractSchemaEntity();
        entity.generationContractId = generationContractId;
        entity.productTypeSchemaId = productTypeSchemaId;
        entity.productTypeId = productTypeId;
        entity.newEntity = true;
        return entity;
    }

    @Override
    public Key getId() {
        return new Key(generationContractId, productTypeSchemaId);
    }

    /** True only for an entity built by create(): save() then persists (INSERT) and never merges over a row. */
    @Override
    public boolean isNew() {
        return newEntity;
    }

    @PostPersist
    @PostLoad
    void markPersisted() {
        newEntity = false;
    }

    public UUID generationContractId() {
        return generationContractId;
    }

    public UUID productTypeSchemaId() {
        return productTypeSchemaId;
    }

    public UUID productTypeId() {
        return productTypeId;
    }
}
