package org.kshrd.webblock.user.application.register.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.register.request.StartRegisterRequest;
import org.kshrd.webblock.user.application.register.response.OtpSentResponse;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.Emails;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.springframework.stereotype.Component;

// Register step 1: check the email, then send a 6-digit code.
// Email already has a password -> "You already have an account". Email exists only through Google -> allowed
// (after the code, the password is added to that same account: one account, two ways to sign in).
@Component
public class StartRegisterHandler {

    private final KeycloakAdminClient keycloak;
    private final OtpManager otp;

    public StartRegisterHandler(KeycloakAdminClient keycloak, OtpManager otp) {
        this.keycloak = keycloak;
        this.otp = otp;
    }

    public OtpSentResponse handle(StartRegisterRequest request) {
        String email = Emails.normalize(request.email());

        keycloak.findUserByEmail(email).ifPresent(user -> {
            if (keycloak.hasPassword(user.id())) {
                throw new ApiException(ErrorCode.ACCOUNT_ALREADY_EXISTS);
            }
        });

        otp.sendCode(OtpPurpose.REGISTER, email, request.fullName().trim());
        return new OtpSentResponse(
                otp.settings().codeTtl().toSeconds(),
                otp.settings().resendAfter().toSeconds());
    }
}
