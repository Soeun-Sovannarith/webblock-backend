package org.kshrd.webblock.project.presentation.rest.page;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.kshrd.webblock.project.application.generation.model.DeployVersion;
import org.kshrd.webblock.project.application.generation.model.DeployVersionPage;
import org.kshrd.webblock.project.application.page.handler.PageVersionService;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.application.page.request.RestoreVersionRequest;
import org.kshrd.webblock.project.application.page.response.PageResponse;
import org.kshrd.webblock.project.application.page.response.PageWriteResponse;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.common.presentation.envelope.PagedResult;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Deploy versions (FR-41): list the website's deploys, restore one into the home page (nothing is deployed). */
@RestController
@RequestMapping("/api/v1/projects/{projectId}/versions")
class PageVersionController {

    private final PageVersionService versions;
    private final CurrentUser currentUser;

    PageVersionController(PageVersionService versions, CurrentUser currentUser) {
        this.versions = versions;
        this.currentUser = currentUser;
    }

    @GetMapping
    PagedResult<DeployVersion> list(
            @PathVariable UUID projectId,
            @RequestParam(defaultValue = "20") @Min(1) @Max(ProjectService.MAX_LIST_LIMIT) int limit,
            @RequestParam(required = false) @Size(max = 100) String cursor) {
        DeployVersionPage page = versions.list(projectId, currentUser.userId(), limit, cursor);
        return new PagedResult<>(page.items(), limit, page.nextCursor());
    }

    @PostMapping("/{versionId}/restore")
    PageWriteResponse restore(
            @PathVariable UUID projectId,
            @PathVariable UUID versionId,
            @Valid @RequestBody RestoreVersionRequest body) {
        PageWrite write = versions.restore(projectId, versionId, currentUser.userId(), body.expectedEditRevision());
        return new PageWriteResponse(PageResponse.of(write.page()), write.editRevision());
    }
}
