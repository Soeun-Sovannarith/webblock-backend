package org.kshrd.webblock.notification.application.notification.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import java.io.Serializable;
import java.util.Collections;
import java.util.Map;

/**
 * Inbound notification event record matching the message payload structure:
 * (eventId, eventType, recipientRole, recipientId, recipientEmail, recipientName, Map<String, Object> data)
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record NotificationEventDto(
        @NotBlank(message = "eventId is required")
        @JsonProperty("eventId")
        String eventId,

        @NotBlank(message = "eventType is required")
        @JsonProperty("eventType")
        String eventType,

        @NotBlank(message = "recipientRole is required")
        @JsonProperty("recipientRole")
        String recipientRole,

        @NotBlank(message = "recipientId is required")
        @JsonProperty("recipientId")
        String recipientId,

        @NotBlank(message = "recipientEmail is required")
        @Email(message = "recipientEmail must be a valid email address")
        @JsonProperty("recipientEmail")
        String recipientEmail,

        @NotBlank(message = "recipientName is required")
        @JsonProperty("recipientName")
        String recipientName,

        @JsonProperty("data")
        Map<String, Object> data
) implements Serializable {

    /**
     * Compact constructor providing null-safety and map immutability.
     */
    public NotificationEventDto {
        data = (data != null) ? Collections.unmodifiableMap(data) : Collections.emptyMap();
    }
}
