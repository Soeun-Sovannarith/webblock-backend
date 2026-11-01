package org.kshrd.webblock.project.application.project.model;

import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.application.messaging.model.IntegrationEvent;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * {@value #TYPE}: the owner deleted the website. The envelope only (Provisioning's schema has no other field); the
 * correlation ID is the project ID. Published in the transaction that sets deleted_at. RabbitMQ bodies are snake_case.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record ProjectDeleted(
        int schemaVersion, UUID messageId, String messageType, Instant occurredAt, String correlationId, UUID projectId)
        implements IntegrationEvent {

    public static final String TYPE = "project.project_deleted.v1";

    public static ProjectDeleted of(UUID projectId, Instant now) {
        return new ProjectDeleted(1, Uuids.v7(), TYPE, now, projectId.toString(), projectId);
    }
}
