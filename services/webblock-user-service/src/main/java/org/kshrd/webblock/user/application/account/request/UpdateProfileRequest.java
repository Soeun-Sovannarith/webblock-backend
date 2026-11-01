package org.kshrd.webblock.user.application.account.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

// Settings › Profile: only the full name can change (email cannot).
public record UpdateProfileRequest(
        @NotBlank(message = "Full name is required.")
        @Size(max = 255, message = "At most 255 characters.")
        String fullName) {
}
