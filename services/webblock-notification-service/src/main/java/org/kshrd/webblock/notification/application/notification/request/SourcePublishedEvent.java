package org.kshrd.webblock.notification.application.notification.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotNull;

import java.util.UUID;

/** Incoming project.source_published.v1 (contracts/events/project). Only the fields this service needs. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SourcePublishedEvent(
        @NotNull @JsonProperty("message_id") UUID messageId,
        @NotNull @JsonProperty("project_id") UUID projectId,
        @JsonProperty("generation_request_id") UUID generationRequestId) {
}
