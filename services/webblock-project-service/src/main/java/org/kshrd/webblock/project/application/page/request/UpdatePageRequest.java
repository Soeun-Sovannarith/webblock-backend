package org.kshrd.webblock.project.application.page.request;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;
import org.kshrd.webblock.project.application.page.handler.PageRules;
import org.kshrd.webblock.project.application.page.model.PageContent;
import org.kshrd.webblock.project.common.application.validation.PagePath;
import org.kshrd.webblock.project.common.domain.support.PagePaths;
import tools.jackson.databind.JsonNode;

public record UpdatePageRequest(
        @NotNull @PositiveOrZero Long expectedEditRevision,
        @NotBlank @Size(max = PageRules.MAX_NAME_LENGTH) String name,
        @NotNull @Size(max = PagePaths.MAX_LENGTH) @PagePath String path,
        @NotNull JsonNode puckTree,
        @Size(max = PageRules.MAX_SEO_TITLE_LENGTH) String seoTitle,
        @Size(max = PageRules.MAX_SEO_DESCRIPTION_LENGTH) String seoDescription,

        @NotNull @PositiveOrZero @Max(PageRules.MAX_SORT_ORDER)
        Integer sortOrder) {

    /**
     * Jackson reads "puckTree": null as a JSON null node; it is as missing as an absent tree, so @NotNull refuses both
     * with the same field error.
     */
    public UpdatePageRequest {
        puckTree = puckTree == null || puckTree.isNull() ? null : puckTree;
    }

    public PageContent content() {
        return new PageContent(name, path, puckTree, seoTitle, seoDescription);
    }
}
