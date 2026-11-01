package org.kshrd.webblock.user.application.register.request;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;

// "Resend code"
public record ResendOtpRequest(
        @NotBlank(message = "Email is required.")
        @Email(message = "Email is not valid.")
        String email) {
}
