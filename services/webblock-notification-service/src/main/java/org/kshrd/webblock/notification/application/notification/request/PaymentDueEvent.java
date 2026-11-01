package org.kshrd.webblock.notification.application.notification.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * Incoming payment.payment_due.v1: the monthly payment is due soon (payment-service decides when, e.g. 7 days before).
 * The contract file does not exist yet; these field names are the proposal sent to the payment team.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record PaymentDueEvent(
        @NotNull @JsonProperty("message_id") UUID messageId,
        @NotBlank @JsonProperty("event_key") String eventKey,
        @NotNull @JsonProperty("user_id") UUID userId,
        @NotNull @JsonProperty("subscription_id") UUID subscriptionId,
        @NotNull @JsonProperty("plan_id") UUID planId,
        @NotNull @JsonProperty("amount_usd") BigDecimal amountUsd,
        @NotNull @JsonProperty("due_at") Instant dueAt) {
}
