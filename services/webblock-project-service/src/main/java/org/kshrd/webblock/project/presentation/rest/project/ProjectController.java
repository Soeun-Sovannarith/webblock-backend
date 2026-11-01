package org.kshrd.webblock.project.presentation.rest.project;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.kshrd.webblock.project.application.project.handler.ProjectLifecycleService;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.handler.ProjectViewAssembler;
import org.kshrd.webblock.project.application.project.model.ProjectPage;
import org.kshrd.webblock.project.application.project.model.ProjectView;
import org.kshrd.webblock.project.application.project.response.ProjectCounts;
import org.kshrd.webblock.project.common.presentation.envelope.PagedResult;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** "My websites": only the owner sees or changes a project; anyone else gets PROJECT_NOT_FOUND (spec 6.1). */
@RestController
@RequestMapping("/api/v1/projects")
class ProjectController {

    private final ProjectService projects;
    private final ProjectLifecycleService lifecycle;
    private final ProjectViewAssembler views;
    private final CurrentUser currentUser;

    ProjectController(
            ProjectService projects,
            ProjectLifecycleService lifecycle,
            ProjectViewAssembler views,
            CurrentUser currentUser) {
        this.projects = projects;
        this.lifecycle = lifecycle;
        this.views = views;
        this.currentUser = currentUser;
    }

    @GetMapping
    PagedResult<ProjectView> list(
            @RequestParam(defaultValue = "20") @Min(1) @Max(ProjectService.MAX_LIST_LIMIT) int limit,
            @RequestParam(required = false) @Size(max = 100) String cursor,
            @RequestParam(required = false) @Size(max = ProjectService.MAX_SEARCH_LENGTH) String q,
            @RequestParam(required = false) String status) {
        ProjectPage page = projects.listOwned(currentUser.userId(), limit, cursor, q, status);
        return new PagedResult<>(views.views(page.items()), limit, page.nextCursor());
    }

    @GetMapping("/counts")
    ProjectCounts counts() {
        return projects.countOwned(currentUser.userId());
    }

    @GetMapping("/{projectId}")
    ProjectView get(@PathVariable UUID projectId) {
        return views.view(projects.getOwned(projectId, currentUser.userId()));
    }

    @DeleteMapping("/{projectId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void delete(@PathVariable UUID projectId) {
        lifecycle.delete(projectId, currentUser.userId());
    }

    @PostMapping("/{projectId}/suspend")
    ProjectView suspend(@PathVariable UUID projectId) {
        return views.view(lifecycle.suspend(projectId, currentUser.userId()));
    }

    @PostMapping("/{projectId}/reactivate")
    ProjectView reactivate(@PathVariable UUID projectId) {
        return views.view(lifecycle.reactivate(projectId, currentUser.userId()));
    }
}
