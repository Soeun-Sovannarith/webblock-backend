package org.kshrd.webblock.notification.application.notification.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.util.UUID;

/** Incoming payment.hosting_entitlement_ended.v1. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record HostingEntitlementEndedEvent(
        @NotNull @JsonProperty("message_id") UUID messageId,
        @NotNull @JsonProperty("user_id") UUID userId,
        @NotBlank @JsonProperty("event_key") String eventKey) {
}
