package org.kshrd.webblock.project.presentation.rest.generation;

import java.util.UUID;
import org.kshrd.webblock.project.application.generation.handler.CodegenInputService;
import org.kshrd.webblock.project.application.generation.model.CodegenInput;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** AI Codegen's read of a request's captured input. No token: the mesh policy for ai-codegen is the gate. */
@RestController
@RequestMapping("/internal/v1/generation-requests")
class CodegenInputController {

    private final CodegenInputService service;

    CodegenInputController(CodegenInputService service) {
        this.service = service;
    }

    @GetMapping("/{generationRequestId}/codegen-input")
    CodegenInput codegenInput(@PathVariable UUID generationRequestId) {
        return service.codegenInput(generationRequestId);
    }
}
