package org.kshrd.webblock.user.application.register.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

// Register step 3: create the password. The rules match the Keycloak password policy
// (at least 8, one upper case, one lower case, one digit); Keycloak checks them again.
public record CompleteRegisterRequest(
        @NotBlank(message = "Verification token is required.")
        String verificationToken,

        @NotBlank(message = "Password is required.")
        @Size(min = 8, max = 128, message = "Use 8 to 128 characters.")
        @Pattern(regexp = "^(?=.*[a-z])(?=.*[A-Z])(?=.*\\d).*$",
                message = "Use at least one upper case letter, one lower case letter and one digit.")
        String password,

        @NotBlank(message = "Please confirm the password.")
        String confirmPassword) {
}
