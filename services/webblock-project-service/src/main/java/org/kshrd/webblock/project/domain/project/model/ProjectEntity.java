package org.kshrd.webblock.project.domain.project.model;

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
import org.hibernate.dialect.type.PostgreSQLEnumJdbcType;
import org.springframework.data.domain.Persistable;

@Entity
@Table(schema = "project", name = "projects")
public class ProjectEntity implements Persistable<UUID> {

    @Id
    @Column(name = "project_id", nullable = false, updatable = false)
    private UUID projectId;

    @Column(name = "owner_user_id", nullable = false, updatable = false)
    private UUID ownerUserId;

    @Column(name = "name", nullable = false, length = 255)
    private String name;

    @Column(name = "subdomain", nullable = false, updatable = false, length = 63)
    private String subdomain;

    @Column(name = "business_type_id", nullable = false, updatable = false)
    private UUID businessTypeId;

    @Column(name = "edit_revision", nullable = false)
    private long editRevision;

    @Column(name = "component_catalog_version", nullable = false, length = 100)
    private String componentCatalogVersion;

    @Column(name = "repository_id")
    private Long repositoryId;

    @Column(name = "repository_full_name", length = 140)
    private String repositoryFullName;

    @Enumerated(EnumType.STRING)
    @JdbcType(PostgreSQLEnumJdbcType.class)
    @Column(name = "status", nullable = false, columnDefinition = "project_status")
    private ProjectStatus status;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "deleted_at")
    private Instant deletedAt;

    @Transient
    private boolean newEntity;

    protected ProjectEntity() {}

    /** The domain layer knows no application type, so the new project's fields come one by one. */
    public static ProjectEntity draft(
            UUID projectId,
            UUID ownerUserId,
            String name,
            String subdomain,
            UUID businessTypeId,
            String componentCatalogVersion,
            Instant now) {
        ProjectEntity entity = new ProjectEntity();
        entity.projectId = projectId;
        entity.ownerUserId = ownerUserId;
        entity.name = name;
        entity.subdomain = subdomain;
        entity.businessTypeId = businessTypeId;
        entity.editRevision = 0;
        entity.componentCatalogVersion = componentCatalogVersion;
        entity.status = ProjectStatus.draft;
        entity.createdAt = now;
        entity.updatedAt = now;
        entity.newEntity = true;
        return entity;
    }

    @Override
    public UUID getId() {
        return projectId;
    }

    /** True only for an entity built by draft(): save() then persists (INSERT) and never merges over a row. */
    @Override
    public boolean isNew() {
        return newEntity;
    }

    @PostPersist
    @PostLoad
    void markPersisted() {
        newEntity = false;
    }

    public UUID ownerUserId() {
        return ownerUserId;
    }

    public String name() {
        return name;
    }

    public String subdomain() {
        return subdomain;
    }

    public UUID businessTypeId() {
        return businessTypeId;
    }

    public long editRevision() {
        return editRevision;
    }

    public String componentCatalogVersion() {
        return componentCatalogVersion;
    }

    public ProjectStatus status() {
        return status;
    }

    public Instant createdAt() {
        return createdAt;
    }

    public Instant updatedAt() {
        return updatedAt;
    }

    public Instant deletedAt() {
        return deletedAt;
    }
}
