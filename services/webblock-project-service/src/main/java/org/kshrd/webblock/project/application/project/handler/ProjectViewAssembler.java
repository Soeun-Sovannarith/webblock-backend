package org.kshrd.webblock.project.application.project.handler;

import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.project.model.ProjectView;
import org.kshrd.webblock.project.application.template.handler.TemplateCatalog;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Adds what the website list and detail show beyond the project row (FR-14, FR-15): the business type code and name
 * (one catalog query for the whole page) and the template thumbnail (one pages query for the whole page, the home
 * page's style key mapped through the bundled templates). A project with no known style key has a null thumbnail. If
 * the catalog is down the names are null and the read still answers, so the editor stays usable. It also adds
 * {@code lastDeployedEditRevision} (the highest edit revision captured by a published generation request, one grouped
 * query for the whole page) and {@code hasUndeployedChanges}: true for a website never deployed, otherwise true when
 * its edit revision is above the last deployed one.
 */
@Component
public class ProjectViewAssembler {

    private static final Logger LOG = LoggerFactory.getLogger(ProjectViewAssembler.class);

    private final CatalogReference catalog;
    private final PageService pages;
    private final TemplateCatalog templates;
    private final GenerationRequestService requests;

    public ProjectViewAssembler(
            CatalogReference catalog, PageService pages, TemplateCatalog templates, GenerationRequestService requests) {
        this.catalog = catalog;
        this.pages = pages;
        this.templates = templates;
        this.requests = requests;
    }

    public ProjectView view(ProjectRecord project) {
        return views(List.of(project)).getFirst();
    }

    public List<ProjectView> views(List<ProjectRecord> projects) {
        if (projects.isEmpty()) {
            return List.of();
        }
        Map<UUID, BusinessType> types = businessTypes(projects);
        Map<UUID, String> styleKeys = pages.homeTemplateKeys(
                projects.stream().map(ProjectRecord::projectId).toList());
        Map<UUID, Long> deployed = requests.lastDeployedEditRevisions(
                projects.stream().map(ProjectRecord::projectId).toList());
        return projects.stream()
                .map(project -> {
                    BusinessType type = types.get(project.businessTypeId());
                    String thumbnail = templates
                            .thumbnailForStyleKey(styleKeys.get(project.projectId()))
                            .orElse(null);
                    return ProjectView.of(
                            project,
                            type == null ? null : type.code(),
                            type == null ? null : type.name(),
                            thumbnail,
                            deployed.get(project.projectId()));
                })
                .toList();
    }

    private Map<UUID, BusinessType> businessTypes(Collection<ProjectRecord> projects) {
        try {
            return catalog.businessTypes(projects.stream()
                    .map(ProjectRecord::businessTypeId)
                    .distinct()
                    .toList());
        } catch (WebBlockException e) {
            if (e.errorCode() != ErrorCode.CATALOG_UNAVAILABLE) {
                throw e;
            }
            LOG.warn("Catalog unavailable: business type names left out of the project read");
            return Map.of();
        }
    }
}
