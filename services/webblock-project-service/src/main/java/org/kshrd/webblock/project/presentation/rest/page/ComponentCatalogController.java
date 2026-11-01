package org.kshrd.webblock.project.presentation.rest.page;

import org.kshrd.webblock.project.application.page.handler.PuckValidator;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/** Block definitions for the editor (spec 6.1). */
@RestController
class ComponentCatalogController {

    private final PuckValidator validator;

    ComponentCatalogController(PuckValidator validator) {
        this.validator = validator;
    }

    @GetMapping("/api/v1/component-catalog/{version}")
    JsonNode get(@PathVariable String version) {
        return validator
                .catalogDocument(version)
                .orElseThrow(() -> new WebBlockException(ErrorCode.COMPONENT_CATALOG_NOT_FOUND));
    }
}
