package org.kshrd.webblock.user.application.register.request;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

// Register step 2: the 6-digit code from the email
public record VerifyOtpRequest(
        @NotBlank(message = "Email is required.")
        @Email(message = "Email is not valid.")
        String email,

        @NotBlank(message = "Code is required.")
        @Pattern(regexp = "\\d{6}", message = "The code has 6 digits.")
        String code) {
}
