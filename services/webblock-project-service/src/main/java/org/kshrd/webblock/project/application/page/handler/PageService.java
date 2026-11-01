package org.kshrd.webblock.project.application.page.handler;

import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.page.model.PageContent;
import org.kshrd.webblock.project.application.page.model.PageList;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.page.model.PageEntity;
import org.kshrd.webblock.project.infrastructure.persistence.page.PageRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

/**
 * Pages under revision 19's rules: a website has exactly one page (home, "/"), so there is no add or delete. Every write
 * moves the project's edit_revision from the expected value (a stale value is STALE_EDIT_REVISION), paths are unique
 * among live pages. Only the owner reads or writes; a deleted or suspended project is read-only. The content is
 * checked against the page rules (PageRules) before anything changes.
 */
@Service
public class PageService {

    private final PageRepository pages;
    private final ProjectService projects;
    private final PageRules rules;
    private final JsonMapper json;
    private final Clock clock;

    public PageService(PageRepository pages, ProjectService projects, PageRules rules, JsonMapper json, Clock clock) {
        this.pages = pages;
        this.projects = projects;
        this.rules = rules;
        this.json = json;
        this.clock = clock;
    }

    @Transactional(readOnly = true)
    public PageList list(UUID projectId, UUID owner) {
        ProjectRecord project = projects.getOwned(projectId, owner);
        return new PageList(
                project.editRevision(),
                pages.findLive(projectId).stream().map(this::toRecord).toList());
    }

    /** The live pages in page order, for a caller that holds the owner's project lock (no owner check here). */
    @Transactional(propagation = Propagation.MANDATORY)
    public List<PageRecord> livePages(UUID projectId) {
        return pages.findLive(projectId).stream().map(this::toRecord).toList();
    }

    /**
     * The style key (root.props.templateId) of each project's live home page, for the projects that have one; one
     * query. No owner check: the caller passes projects it already read as the owner's.
     */
    @Transactional(readOnly = true)
    public Map<UUID, String> homeTemplateKeys(Collection<UUID> projectIds) {
        if (projectIds.isEmpty()) {
            return Map.of();
        }
        Map<UUID, String> keys = new HashMap<>();
        for (PageRepository.HomeTemplateKey row : pages.homeTemplateKeys(projectIds)) {
            if (row.getTemplateId() != null) {
                keys.put(row.getProjectId(), row.getTemplateId());
            }
        }
        return keys;
    }

    @Transactional
    public PageWrite update(
            UUID projectId, UUID pageId, UUID owner, long expectedEditRevision, PageContent content, int sortOrder) {
        ProjectRecord project = writable(projectId, owner);
        rules.checkSortOrder(sortOrder);
        String tree = rules.checkedTree(project.componentCatalogVersion(), content.puckTree());
        long revision = projects.advanceEditRevision(projectId, owner, expectedEditRevision);
        // Loaded after the revision moved: that update clears the persistence context.
        PageEntity page = live(projectId, pageId);
        page.apply(
                content.name(), content.path(), tree, content.seoTitle(), content.seoDescription(), sortOrder, now());
        return new PageWrite(toRecord(pages.saveAndFlush(page)), revision);
    }

    /** The wizard's template pages, in the wizard's transaction; a new project's revision stays 0. */
    @Transactional(propagation = Propagation.MANDATORY)
    public void insertInitialPages(UUID projectId, String componentCatalogVersion, List<NewPage> initial) {
        if (initial.size() != 1) {
            throw new IllegalArgumentException("A website has exactly 1 page");
        }
        Instant now = now();
        for (NewPage p : initial) {
            pages.saveAndFlush(PageEntity.create(
                    Uuids.v7(),
                    projectId,
                    p.name(),
                    p.path(),
                    rules.checkedPage(componentCatalogVersion, p),
                    p.seoTitle(),
                    p.seoDescription(),
                    p.sortOrder(),
                    now));
        }
    }

    /** The caller's project, refused as read-only before its content is checked (the owner can fix neither). */
    private ProjectRecord writable(UUID projectId, UUID owner) {
        ProjectRecord project = projects.getOwned(projectId, owner);
        if (project.readOnly()) {
            throw new WebBlockException(ErrorCode.PROJECT_READ_ONLY);
        }
        return project;
    }

    private PageEntity live(UUID projectId, UUID pageId) {
        return pages.findLive(projectId, pageId).orElseThrow(() -> new WebBlockException(ErrorCode.PAGE_NOT_FOUND));
    }

    private PageRecord toRecord(PageEntity page) {
        return new PageRecord(
                page.getId(),
                page.projectId(),
                page.name(),
                page.path(),
                json.readTree(page.puckTree()),
                page.seoTitle(),
                page.seoDescription(),
                page.sortOrder(),
                page.createdAt(),
                page.updatedAt());
    }

    private Instant now() {
        return clock.instant().truncatedTo(ChronoUnit.MICROS);
    }
}
