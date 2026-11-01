package org.kshrd.webblock.user.application.register.request;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

// Register step 1: full name + email
public record StartRegisterRequest(
        @NotBlank(message = "Full name is required.")
        @Size(max = 255, message = "At most 255 characters.")
        String fullName,

        @NotBlank(message = "Email is required.")
        @Email(message = "Email is not valid.")
        @Size(max = 320, message = "At most 320 characters.")
        String email) {
}
