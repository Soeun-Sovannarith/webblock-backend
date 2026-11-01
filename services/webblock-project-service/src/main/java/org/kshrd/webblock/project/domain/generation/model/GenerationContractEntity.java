package org.kshrd.webblock.project.domain.generation.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.Instant;
import java.util.UUID;
import org.hibernate.annotations.Immutable;
import org.springframework.data.domain.Persistable;

/** A frozen generation contract: one version of the product-type schemas a project's generated code targets. */
@Entity
@Immutable
@Table(schema = "project", name = "generation_contracts")
public class GenerationContractEntity implements Persistable<UUID> {

    @Id
    @Column(name = "generation_contract_id", nullable = false, updatable = false)
    private UUID generationContractId;

    @Column(name = "project_id", nullable = false, updatable = false)
    private UUID projectId;

    @Column(name = "version_number", nullable = false, updatable = false)
    private int versionNumber;

    @Column(name = "api_contract_version", nullable = false, updatable = false, length = 100)
    private String apiContractVersion;

    @Column(name = "created_by_user_id", nullable = false, updatable = false)
    private UUID createdByUserId;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Transient
    private boolean newEntity;

    protected GenerationContractEntity() {}

    public static GenerationContractEntity create(
            UUID generationContractId,
            UUID projectId,
            int versionNumber,
            String apiContractVersion,
            UUID createdByUserId,
            Instant createdAt) {
        GenerationContractEntity entity = new GenerationContractEntity();
        entity.generationContractId = generationContractId;
        entity.projectId = projectId;
        entity.versionNumber = versionNumber;
        entity.apiContractVersion = apiContractVersion;
        entity.createdByUserId = createdByUserId;
        entity.createdAt = createdAt;
        entity.newEntity = true;
        return entity;
    }

    @Override
    public UUID getId() {
        return generationContractId;
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

    public UUID projectId() {
        return projectId;
    }

    public int versionNumber() {
        return versionNumber;
    }

    public String apiContractVersion() {
        return apiContractVersion;
    }

    public UUID createdByUserId() {
        return createdByUserId;
    }

    public Instant createdAt() {
        return createdAt;
    }
}
