package org.kshrd.webblock.user.application.account.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

// Settings › Password. currentPassword is required when the user already has a password;
// a Google-only user (no password yet) leaves it empty to SET a first password.
// The new-password rules match the Keycloak password policy; Keycloak checks them again.
public record ChangePasswordRequest(
        @Size(max = 128, message = "At most 128 characters.")
        String currentPassword,

        @NotBlank(message = "New password is required.")
        @Size(min = 8, max = 128, message = "Use 8 to 128 characters.")
        @Pattern(regexp = "^(?=.*[a-z])(?=.*[A-Z])(?=.*\\d).*$",
                message = "Use at least one upper case letter, one lower case letter and one digit.")
        String newPassword,

        @NotBlank(message = "Please confirm the password.")
        String confirmPassword) {
}
