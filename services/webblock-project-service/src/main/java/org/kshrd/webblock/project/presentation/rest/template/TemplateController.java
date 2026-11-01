package org.kshrd.webblock.project.presentation.rest.template;

import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.template.handler.TemplateCatalog;
import org.kshrd.webblock.project.application.template.model.SiteTemplate;
import org.kshrd.webblock.project.application.template.response.TemplateDetailResponse;
import org.kshrd.webblock.project.application.template.response.TemplateResponse;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Wizard step 2: the templates, all of them or those of the chosen business type. */
@RestController
class TemplateController {

    private final TemplateCatalog templates;
    private final CatalogReference catalog;

    TemplateController(TemplateCatalog templates, CatalogReference catalog) {
        this.templates = templates;
        this.catalog = catalog;
    }

    @GetMapping("/api/v1/templates/{templateCode}")
    TemplateDetailResponse get(@PathVariable String templateCode) {
        return templates
                .find(templateCode)
                .map(TemplateDetailResponse::of)
                .orElseThrow(() -> new WebBlockException(ErrorCode.TEMPLATE_NOT_FOUND));
    }

    @GetMapping("/api/v1/templates")
    List<TemplateResponse> list(
            @RequestParam(required = false) @Pattern(regexp = ".*\\S.*") @Size(max = 50) String businessTypeCode) {
        List<SiteTemplate> found;
        if (businessTypeCode == null) {
            found = templates.all();
        } else if (catalog.activeBusinessType(businessTypeCode).isEmpty()) {
            found = List.of();
        } else {
            found = templates.forBusinessType(businessTypeCode);
        }
        return found.stream()
                .map(t -> new TemplateResponse(
                        t.templateCode(),
                        t.name(),
                        t.description(),
                        t.thumbnailUrl(),
                        t.badge(),
                        t.styleKey(),
                        t.version(),
                        t.pages().size()))
                .toList();
    }
}
