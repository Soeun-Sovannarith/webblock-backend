package org.kshrd.webblock.notification.application.alert.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Body Alertmanager POSTs to a webhook receiver (webhook payload version 4). Only the fields this service needs.
 * The label names this service reads are listed in {@code HandleAlertmanagerWebhookHandler}.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AlertmanagerWebhookRequest(
        @NotNull @Size(max = 1000) List<Alert> alerts) {

    /** One alert. {@code status} is "firing" or "resolved"; {@code fingerprint} + {@code startsAt} identify one incident. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Alert(
            String status,
            Map<String, String> labels,
            String startsAt,
            String fingerprint) {

        public Alert {
            labels = labels == null ? Map.of() : Collections.unmodifiableMap(new HashMap<>(labels));
        }
    }
}
