package org.kshrd.webblock.project.presentation.rest.generation;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.net.URI;
import java.util.UUID;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.application.generation.model.CaptureOutcome;
import org.kshrd.webblock.project.application.generation.model.GenerationRequestPage;
import org.kshrd.webblock.project.application.generation.model.GenerationRequestRecord;
import org.kshrd.webblock.project.application.generation.request.CaptureGenerationRequest;
import org.kshrd.webblock.project.application.generation.response.CaptureResponse;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.common.application.validation.IdempotencyKey;
import org.kshrd.webblock.project.common.presentation.envelope.PagedResult;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Generation requests (spec 5.3.3): owner-only. A capture answers 202 with a Location; its idempotent replay answers
 * 200 with the same body, the status as it is now (decisions J2-15, J2-26).
 */
@RestController
@RequestMapping("/api/v1/projects/{projectId}/generation-requests")
class GenerationRequestController {

    private final GenerationRequestService requests;
    private final CurrentUser currentUser;

    GenerationRequestController(GenerationRequestService requests, CurrentUser currentUser) {
        this.requests = requests;
        this.currentUser = currentUser;
    }

    @PostMapping
    ResponseEntity<CaptureResponse> capture(
            @PathVariable UUID projectId,
            @RequestHeader("Idempotency-Key") @IdempotencyKey String idempotencyKey,
            @Valid @RequestBody(required = false) CaptureGenerationRequest body) {
        UUID contractId = body == null ? null : body.generationContractId();
        CaptureOutcome outcome = requests.capture(projectId, currentUser.userId(), idempotencyKey, contractId);
        CaptureResponse response = new CaptureResponse(outcome.generationRequestId(), outcome.status());
        if (!outcome.created()) {
            return ResponseEntity.ok(response);
        }
        URI location =
                URI.create("/api/v1/projects/" + projectId + "/generation-requests/" + outcome.generationRequestId());
        return ResponseEntity.accepted().location(location).body(response);
    }

    @GetMapping
    PagedResult<GenerationRequestRecord> list(
            @PathVariable UUID projectId,
            @RequestParam(defaultValue = "20") @Min(1) @Max(ProjectService.MAX_LIST_LIMIT) int limit,
            @RequestParam(required = false) @Size(max = 100) String cursor) {
        GenerationRequestPage page = requests.list(projectId, currentUser.userId(), limit, cursor);
        return new PagedResult<>(page.items(), limit, page.nextCursor());
    }

    @GetMapping("/{generationRequestId}")
    GenerationRequestRecord get(@PathVariable UUID projectId, @PathVariable UUID generationRequestId) {
        return requests.get(projectId, currentUser.userId(), generationRequestId);
    }
}
