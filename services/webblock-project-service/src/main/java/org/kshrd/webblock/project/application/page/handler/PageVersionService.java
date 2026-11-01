package org.kshrd.webblock.project.application.page.handler;

import java.util.UUID;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.application.generation.model.DeployVersionPage;
import org.kshrd.webblock.project.application.page.model.PageContent;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Deploy versions (FR-41): the owner lists the website's deploys and restores one. A version is a published generation
 * request; its frozen captured_input still holds the home page as it was deployed. A restore saves that page into
 * the live home page through {@link PageService#update}, so the revision rules, the read-only refusal and the page
 * checks are those of a normal save. Nothing is deployed by a restore.
 */
@Service
public class PageVersionService {

    private static final String HOME = "/";

    private final ProjectService projects;
    private final GenerationRequestService requests;
    private final PageService pages;
    private final JsonMapper json;

    public PageVersionService(
            ProjectService projects, GenerationRequestService requests, PageService pages, JsonMapper json) {
        this.projects = projects;
        this.requests = requests;
        this.pages = pages;
        this.json = json;
    }

    /** The owner's deploys, newest first; a stranger, unknown or deleted project is PROJECT_NOT_FOUND. */
    @Transactional(readOnly = true)
    public DeployVersionPage list(UUID projectId, UUID owner, int limit, String cursor) {
        projects.getOwned(projectId, owner);
        return requests.versions(projectId, limit, cursor);
    }

    /**
     * Saves the home page of the deploy into the live home page. The owner is checked before the version, so a
     * stranger learns nothing about it.
     *
     * @throws WebBlockException PROJECT_NOT_FOUND, VERSION_NOT_FOUND, PROJECT_READ_ONLY, STALE_EDIT_REVISION or what a
     *     page save answers for a tree the current catalog refuses
     */
    @Transactional
    public PageWrite restore(UUID projectId, UUID versionId, UUID owner, long expectedEditRevision) {
        projects.getOwned(projectId, owner);
        JsonNode frozen = homePageOf(json.readTree(requests.versionInput(projectId, versionId)));
        PageRecord home = pages.livePages(projectId).stream()
                .filter(p -> HOME.equals(p.path()))
                .findFirst()
                .orElseThrow(() -> new WebBlockException(ErrorCode.PAGE_NOT_FOUND));
        PageContent content = new PageContent(
                nameOr(frozen, home.name()),
                HOME,
                frozen.get("puckTree"),
                text(frozen, "seoTitle"),
                text(frozen, "seoDescription"));
        return pages.update(
                projectId,
                home.pageId(),
                owner,
                expectedEditRevision,
                content,
                frozen.path("sortOrder").asInt(0));
    }

    /** The home page entry of a captured input; a deploy without one cannot be restored. */
    private static JsonNode homePageOf(JsonNode input) {
        for (JsonNode page : input.path("pages")) {
            if (HOME.equals(page.path("path").asString(null)) && page.hasNonNull("puckTree")) {
                return page;
            }
        }
        throw new WebBlockException(ErrorCode.VERSION_NOT_FOUND);
    }

    private static String nameOr(JsonNode page, String fallback) {
        String name = text(page, "name");
        return name == null || name.isBlank() ? fallback : name;
    }

    private static String text(JsonNode page, String field) {
        return page.hasNonNull(field) ? page.get(field).asString() : null;
    }
}
