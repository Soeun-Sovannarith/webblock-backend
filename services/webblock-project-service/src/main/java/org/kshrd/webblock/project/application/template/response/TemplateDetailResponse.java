package org.kshrd.webblock.project.application.template.response;

import org.kshrd.webblock.project.application.template.model.SiteTemplate;
import org.kshrd.webblock.project.application.template.model.TemplatePage;
import tools.jackson.databind.JsonNode;

/** One template with its one page, for the live preview (FR-9.5): the list item's fields plus {@code page}. */
public record TemplateDetailResponse(
        String templateCode,
        String name,
        String description,
        String thumbnailUrl,
        String badge,
        String styleKey,
        String version,
        int pageCount,
        Page page) {

    public record Page(String name, String path, JsonNode puckTree) {}

    public static TemplateDetailResponse of(SiteTemplate t) {
        TemplatePage home = t.pages().getFirst();
        return new TemplateDetailResponse(
                t.templateCode(),
                t.name(),
                t.description(),
                t.thumbnailUrl(),
                t.badge(),
                t.styleKey(),
                t.version(),
                t.pages().size(),
                new Page(home.name(), home.path(), home.puckTree()));
    }
}
