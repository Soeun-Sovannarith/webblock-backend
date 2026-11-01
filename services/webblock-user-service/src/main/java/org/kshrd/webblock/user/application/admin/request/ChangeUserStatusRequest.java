package org.kshrd.webblock.user.application.admin.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

// Admin › Users › Suspend / Activate.
// reasonCode is required to suspend (ABUSE, FRAUD, OWNER_REQUEST, OTHER); note is a private admin note.
public record ChangeUserStatusRequest(
        @NotBlank(message = "Status is required.")
        @Pattern(regexp = "ACTIVE|SUSPENDED", message = "Use ACTIVE or SUSPENDED.")
        String status,

        @Pattern(regexp = "ABUSE|FRAUD|OWNER_REQUEST|OTHER", message = "Use ABUSE, FRAUD, OWNER_REQUEST or OTHER.")
        String reasonCode,

        @Size(max = 500, message = "At most 500 characters.")
        String note) {
}
