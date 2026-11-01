package org.kshrd.webblock.project.application.page.model;

import java.time.Instant;
import java.util.UUID;
import tools.jackson.databind.JsonNode;

/** A live page as other features and the API see it. */
public record PageRecord(
        UUID pageId,
        UUID projectId,
        String name,
        String path,
        JsonNode puckTree,
        String seoTitle,
        String seoDescription,
        int sortOrder,
        Instant createdAt,
        Instant updatedAt) {}
