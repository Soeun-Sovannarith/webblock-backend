package org.kshrd.webblock.project.domain.page.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.Instant;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import org.springframework.data.domain.Persistable;

@Entity
@Table(schema = "project", name = "pages")
public class PageEntity implements Persistable<UUID> {

    @Id
    @Column(name = "page_id", nullable = false, updatable = false)
    private UUID pageId;

    @Column(name = "project_id", nullable = false, updatable = false)
    private UUID projectId;

    @Column(name = "name", nullable = false, length = 255)
    private String name;

    @Column(name = "path", nullable = false, length = 255)
    private String path;

    /** The tree as JSON text; PostgreSQL stores it as jsonb (spec 4.2). The domain holds no Jackson type. */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "puck_tree", nullable = false, columnDefinition = "jsonb")
    private String puckTree;

    @Column(name = "seo_title", length = 255)
    private String seoTitle;

    @Column(name = "seo_description", length = 500)
    private String seoDescription;

    @Column(name = "sort_order", nullable = false)
    private int sortOrder;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "deleted_at")
    private Instant deletedAt;

    @Transient
    private boolean newEntity;

    protected PageEntity() {}

    /** The domain layer knows no application type, so the new page's fields come one by one. */
    public static PageEntity create(
            UUID pageId,
            UUID projectId,
            String name,
            String path,
            String puckTree,
            String seoTitle,
            String seoDescription,
            int sortOrder,
            Instant now) {
        PageEntity page = new PageEntity();
        page.pageId = pageId;
        page.projectId = projectId;
        page.apply(name, path, puckTree, seoTitle, seoDescription, sortOrder, now);
        page.createdAt = now;
        page.newEntity = true;
        return page;
    }

    public void apply(
            String name,
            String path,
            String puckTree,
            String seoTitle,
            String seoDescription,
            int sortOrder,
            Instant now) {
        this.name = name;
        this.path = path;
        this.puckTree = puckTree;
        this.seoTitle = seoTitle;
        this.seoDescription = seoDescription;
        this.sortOrder = sortOrder;
        this.updatedAt = now;
    }

    @Override
    public UUID getId() {
        return pageId;
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

    public UUID projectId() {
        return projectId;
    }

    public String name() {
        return name;
    }

    public String path() {
        return path;
    }

    public String puckTree() {
        return puckTree;
    }

    public String seoTitle() {
        return seoTitle;
    }

    public String seoDescription() {
        return seoDescription;
    }

    public int sortOrder() {
        return sortOrder;
    }

    public Instant createdAt() {
        return createdAt;
    }

    public Instant updatedAt() {
        return updatedAt;
    }
}
