package org.kshrd.webblock.notification.application.notification.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.util.UUID;

/** Incoming payment.subscription_activated.v1. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SubscriptionActivatedEvent(
        @NotNull @JsonProperty("message_id") UUID messageId,
        @NotBlank @JsonProperty("event_key") String eventKey,
        @NotNull @JsonProperty("subscription_id") UUID subscriptionId,
        @NotNull @JsonProperty("client_user_id") UUID clientUserId,
        @NotNull @JsonProperty("plan_id") UUID planId) {
}
