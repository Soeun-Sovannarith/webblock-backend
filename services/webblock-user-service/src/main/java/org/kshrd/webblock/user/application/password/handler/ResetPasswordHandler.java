package org.kshrd.webblock.user.application.password.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.password.request.ResetPasswordRequest;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.kshrd.webblock.user.infrastructure.persistence.otp.OtpStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

// Forgot password step 3: set the new password in Keycloak, then sign the user out everywhere
// (if someone else knew the old password, they lose access now).
@Component
public class ResetPasswordHandler {

    private static final Logger log = LoggerFactory.getLogger(ResetPasswordHandler.class);

    private final OtpManager otp;
    private final KeycloakAdminClient keycloak;

    public ResetPasswordHandler(OtpManager otp, KeycloakAdminClient keycloak) {
        this.otp = otp;
        this.keycloak = keycloak;
    }

    public void handle(ResetPasswordRequest request) {
        // Check what we can BEFORE using up the one-time token
        if (!request.password().equals(request.confirmPassword())) {
            throw new ApiException(ErrorCode.PASSWORD_MISMATCH);
        }

        OtpStore.Verification verified = otp.takeVerification(OtpPurpose.RESET_PASSWORD, request.verificationToken());
        String email = verified.email();
        if (request.password().equalsIgnoreCase(email)) {
            throw new ApiException(ErrorCode.PASSWORD_TOO_WEAK); // realm policy: not the username (= email)
        }

        KeycloakAdminClient.KeycloakUser user = keycloak.findUserByEmail(email)
                .filter(KeycloakAdminClient.KeycloakUser::enabled)
                .orElseThrow(() -> new ApiException(ErrorCode.VERIFICATION_TOKEN_INVALID));

        keycloak.setPassword(user.id(), request.password());
        keycloak.logoutAllSessions(user.id());
        log.info("Password reset done for user_id={}", user.id());
    }
}
