package org.kshrd.webblock.project.application.page.response;

import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import tools.jackson.databind.JsonNode;

public record PageResponse(
        UUID pageId,
        String name,
        String path,
        JsonNode puckTree,
        String seoTitle,
        String seoDescription,
        int sortOrder,
        Instant updatedAt) {

    public static PageResponse of(PageRecord page) {
        return new PageResponse(
                page.pageId(),
                page.name(),
                page.path(),
                page.puckTree(),
                page.seoTitle(),
                page.seoDescription(),
                page.sortOrder(),
                page.updatedAt());
    }
}
