package org.kshrd.webblock.project.presentation.rest.wizard;

import jakarta.validation.Valid;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.application.project.handler.ProjectViewAssembler;
import org.kshrd.webblock.project.application.project.model.ProjectView;
import org.kshrd.webblock.project.application.wizard.handler.ProjectCreationService;
import org.kshrd.webblock.project.application.wizard.model.CreationOutcome;
import org.kshrd.webblock.project.application.wizard.request.CreateProjectRequest;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Create a website (spec 5.1): idempotent on the client-generated project ID; 201 when created, 200 for a repeat. */
@RestController
class ProjectCreationController {

    private final ProjectCreationService service;
    private final ProjectViewAssembler views;
    private final CurrentUser currentUser;

    ProjectCreationController(ProjectCreationService service, ProjectViewAssembler views, CurrentUser currentUser) {
        this.service = service;
        this.views = views;
        this.currentUser = currentUser;
    }

    /** ADMISSION_RELEASE_PENDING (409) carries Retry-After, set by the exception handler: repeat the same request. */
    @PutMapping("/api/v1/projects/{projectId}")
    ResponseEntity<ProjectView> create(@PathVariable UUID projectId, @Valid @RequestBody CreateProjectRequest body) {
        if ((projectId.getMostSignificantBits() | projectId.getLeastSignificantBits()) == 0) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("projectId", ErrorCode.UUID_INVALID)));
        }
        CreationOutcome outcome = service.create(currentUser.userId(), projectId, body.toCommand());
        return ResponseEntity.status(outcome.created() ? HttpStatus.CREATED : HttpStatus.OK)
                .body(views.view(outcome.project()));
    }
}
