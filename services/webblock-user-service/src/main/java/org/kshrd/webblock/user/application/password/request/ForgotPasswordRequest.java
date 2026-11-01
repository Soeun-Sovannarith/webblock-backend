package org.kshrd.webblock.user.application.password.request;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

// Forgot password step 1 (also used for "Resend code")
public record ForgotPasswordRequest(
        @NotBlank(message = "Email is required.")
        @Email(message = "Email is not valid.")
        @Size(max = 320, message = "At most 320 characters.")
        String email) {
}
