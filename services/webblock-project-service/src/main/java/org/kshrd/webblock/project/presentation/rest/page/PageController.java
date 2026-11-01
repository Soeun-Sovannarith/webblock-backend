package org.kshrd.webblock.project.presentation.rest.page;

import jakarta.validation.Valid;
import java.util.UUID;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.page.model.PageList;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.application.page.request.UpdatePageRequest;
import org.kshrd.webblock.project.application.page.response.PageListResponse;
import org.kshrd.webblock.project.application.page.response.PageResponse;
import org.kshrd.webblock.project.application.page.response.PageWriteResponse;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Pages (spec 5.2 and 6.1); every write sends expectedEditRevision and gets the new editRevision. */
@RestController
@RequestMapping("/api/v1/projects/{projectId}/pages")
class PageController {

    private final PageService pages;
    private final CurrentUser currentUser;

    PageController(PageService pages, CurrentUser currentUser) {
        this.pages = pages;
        this.currentUser = currentUser;
    }

    @GetMapping
    PageListResponse list(@PathVariable UUID projectId) {
        PageList list = pages.list(projectId, currentUser.userId());
        return new PageListResponse(
                list.editRevision(), list.pages().stream().map(PageResponse::of).toList());
    }

    @PutMapping("/{pageId}")
    PageWriteResponse update(
            @PathVariable UUID projectId, @PathVariable UUID pageId, @Valid @RequestBody UpdatePageRequest body) {
        PageWrite write = pages.update(
                projectId, pageId, currentUser.userId(), body.expectedEditRevision(), body.content(), body.sortOrder());
        return new PageWriteResponse(PageResponse.of(write.page()), write.editRevision());
    }
}
