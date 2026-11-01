package org.kshrd.webblock.project.application.template.response;

public record TemplateResponse(
        String templateCode,
        String name,
        String description,
        String thumbnailUrl,
        String badge,
        String styleKey,
        String version,
        int pageCount) {}
