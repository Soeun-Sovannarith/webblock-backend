package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import com.fasterxml.uuid.Generators;
import java.time.Instant;
import java.util.UUID;
import org.kshrd.webblock.project.application.messaging.model.IntegrationEvent;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/** Spike S1's test-only integration event: the six envelope fields and nothing else. */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
record ProbeHappened(
        int schemaVersion, UUID messageId, String messageType, Instant occurredAt, String correlationId, UUID projectId)
        implements IntegrationEvent {

    static final String TYPE = "project.probe_happened.v1";

    static ProbeHappened forProject(UUID projectId) {
        return new ProbeHappened(
                1,
                Generators.timeBasedEpochGenerator().generate(),
                TYPE,
                Instant.now(),
                "corr-" + UUID.randomUUID(),
                projectId);
    }
}
