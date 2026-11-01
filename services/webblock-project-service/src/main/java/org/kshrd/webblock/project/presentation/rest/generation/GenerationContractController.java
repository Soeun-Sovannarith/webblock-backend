package org.kshrd.webblock.project.presentation.rest.generation;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.kshrd.webblock.project.application.generation.handler.GenerationContractService;
import org.kshrd.webblock.project.application.generation.model.GenerationContractPage;
import org.kshrd.webblock.project.application.generation.model.GenerationContractRecord;
import org.kshrd.webblock.project.application.generation.request.CreateGenerationContractRequest;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.common.presentation.envelope.PagedResult;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Generation contracts (spec 5.3.2): owner-only; there is no GET for one contract, so the 201 has no Location. */
@RestController
@RequestMapping("/api/v1/projects/{projectId}/generation-contracts")
class GenerationContractController {

    private final GenerationContractService contracts;
    private final CurrentUser currentUser;

    GenerationContractController(GenerationContractService contracts, CurrentUser currentUser) {
        this.contracts = contracts;
        this.currentUser = currentUser;
    }

    @PostMapping
    ResponseEntity<GenerationContractRecord> create(
            @PathVariable UUID projectId, @Valid @RequestBody CreateGenerationContractRequest body) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(contracts.create(projectId, currentUser.userId(), body.productTypeIds()));
    }

    @GetMapping
    PagedResult<GenerationContractRecord> list(
            @PathVariable UUID projectId,
            @RequestParam(defaultValue = "20") @Min(1) @Max(ProjectService.MAX_LIST_LIMIT) int limit,
            @RequestParam(required = false) @Size(max = 100) String cursor) {
        GenerationContractPage page = contracts.list(projectId, currentUser.userId(), limit, cursor);
        return new PagedResult<>(page.items(), limit, page.nextCursor());
    }
}
