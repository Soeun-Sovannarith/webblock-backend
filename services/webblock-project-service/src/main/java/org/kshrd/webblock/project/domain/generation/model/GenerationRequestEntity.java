package org.kshrd.webblock.project.domain.generation.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.Instant;
import java.util.UUID;
import org.hibernate.annotations.JdbcType;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.dialect.type.PostgreSQLEnumJdbcType;
import org.hibernate.type.SqlTypes;
import org.springframework.data.domain.Persistable;

/**
 * A generation request (spec 5.3). The capture columns are frozen: never updated by the application, refused by the
 * project_app column grants and by the generation_requests_guard trigger. Status changes go through the conditional
 * UPDATE of GenerationRequestRepository.transition, never through this entity.
 */
@Entity
@Table(schema = "project", name = "generation_requests")
public class GenerationRequestEntity implements Persistable<UUID> {

    @Id
    @Column(name = "generation_request_id", nullable = false, updatable = false)
    private UUID generationRequestId;

    @Column(name = "project_id", nullable = false, updatable = false)
    private UUID projectId;

    @Column(name = "idempotency_key", nullable = false, updatable = false, length = 255)
    private String idempotencyKey;

    @JdbcTypeCode(SqlTypes.CHAR)
    @Column(name = "request_hash", nullable = false, updatable = false, length = 64)
    private String requestHash;

    @Column(name = "generation_contract_id", nullable = false, updatable = false)
    private UUID generationContractId;

    @Column(name = "captured_edit_revision", nullable = false, updatable = false)
    private long capturedEditRevision;

    /** The captured input as JSON text; PostgreSQL stores it as jsonb. The domain holds no Jackson type. */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "captured_input", nullable = false, updatable = false, columnDefinition = "jsonb")
    private String capturedInput;

    @JdbcTypeCode(SqlTypes.CHAR)
    @Column(name = "captured_input_hash", nullable = false, updatable = false, length = 64)
    private String capturedInputHash;

    @Column(name = "accepted_generation_run_id")
    private UUID acceptedGenerationRunId;

    @Column(name = "source_commit_sha", length = 64)
    private String sourceCommitSha;

    @Enumerated(EnumType.STRING)
    @JdbcType(PostgreSQLEnumJdbcType.class)
    @Column(name = "status", nullable = false, columnDefinition = "generation_request_status")
    private GenerationRequestStatus status;

    @Column(name = "failure_code", length = 100)
    private String failureCode;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Transient
    private boolean newEntity;

    protected GenerationRequestEntity() {}

    /** A request just captured; the domain knows no application type, so its fields come one by one. */
    public static GenerationRequestEntity captured(
            UUID generationRequestId,
            UUID projectId,
            String idempotencyKey,
            String requestHash,
            UUID generationContractId,
            long capturedEditRevision,
            String capturedInputJson,
            String capturedInputHash,
            Instant now) {
        GenerationRequestEntity entity = new GenerationRequestEntity();
        entity.generationRequestId = generationRequestId;
        entity.projectId = projectId;
        entity.idempotencyKey = idempotencyKey;
        entity.requestHash = requestHash;
        entity.generationContractId = generationContractId;
        entity.capturedEditRevision = capturedEditRevision;
        entity.capturedInput = capturedInputJson;
        entity.capturedInputHash = capturedInputHash;
        entity.status = GenerationRequestStatus.captured;
        entity.createdAt = now;
        entity.updatedAt = now;
        entity.newEntity = true;
        return entity;
    }

    @Override
    public UUID getId() {
        return generationRequestId;
    }

    /** True only for an entity built by captured(): save() then persists (INSERT) and never merges over a row. */
    @Override
    public boolean isNew() {
        return newEntity;
    }

    @PostPersist
    @PostLoad
    void markPersisted() {
        newEntity = false;
    }

    public UUID generationRequestId() {
        return generationRequestId;
    }

    public UUID projectId() {
        return projectId;
    }

    public String idempotencyKey() {
        return idempotencyKey;
    }

    public String requestHash() {
        return requestHash;
    }

    public UUID generationContractId() {
        return generationContractId;
    }

    public long capturedEditRevision() {
        return capturedEditRevision;
    }

    public String capturedInput() {
        return capturedInput;
    }

    public String capturedInputHash() {
        return capturedInputHash;
    }

    public UUID acceptedGenerationRunId() {
        return acceptedGenerationRunId;
    }

    public String sourceCommitSha() {
        return sourceCommitSha;
    }

    public GenerationRequestStatus status() {
        return status;
    }

    public String failureCode() {
        return failureCode;
    }

    public Instant createdAt() {
        return createdAt;
    }

    public Instant updatedAt() {
        return updatedAt;
    }
}
