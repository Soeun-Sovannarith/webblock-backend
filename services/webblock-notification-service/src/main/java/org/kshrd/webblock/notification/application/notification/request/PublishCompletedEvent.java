package org.kshrd.webblock.notification.application.notification.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.util.UUID;

/** Incoming provisioning.publish_completed.v1. status is LIVE, FAILED or ROLLED_BACK (unknown values are ignored). */
@JsonIgnoreProperties(ignoreUnknown = true)
public record PublishCompletedEvent(
        @NotNull @JsonProperty("message_id") UUID messageId,
        @NotNull @JsonProperty("project_id") UUID projectId,
        @NotNull @JsonProperty("release_id") UUID releaseId,
        @NotBlank @JsonProperty("status") String status,
        @NotBlank @JsonProperty("event_key") String eventKey,
        @JsonProperty("failure_code") String failureCode) {
}
