package org.kshrd.webblock.project.application.generation.model;

import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;

/**
 * A generation request as the owner sees it: the list item and the body of GET one (J2-15, J2-23). The captured input
 * is never part of it. failureCode is set only for a failed request.
 */
public record GenerationRequestRecord(
        UUID generationRequestId,
        GenerationRequestStatus status,
        String failureCode,
        Instant createdAt,
        Instant updatedAt) {}
