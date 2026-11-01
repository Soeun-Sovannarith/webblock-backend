package org.kshrd.webblock.project.application.generation.model;

import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.application.messaging.model.IntegrationEvent;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * {@value #TYPE}: Payment admitted the request and it is generating. The envelope plus the request's ID and the hash
 * of its captured input; the correlation ID is the request ID. RabbitMQ bodies are snake_case.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record GenerationRequested(
        int schemaVersion,
        UUID messageId,
        String messageType,
        Instant occurredAt,
        String correlationId,
        UUID projectId,
        UUID generationRequestId,
        String capturedInputHash)
        implements IntegrationEvent {

    public static final String TYPE = "project.generation_requested.v1";

    public static GenerationRequested of(
            UUID projectId, UUID generationRequestId, String capturedInputHash, Instant now) {
        return new GenerationRequested(
                1,
                Uuids.v7(),
                TYPE,
                now,
                generationRequestId.toString(),
                projectId,
                generationRequestId,
                capturedInputHash);
    }
}
