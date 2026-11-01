package org.kshrd.webblock.user.application.password.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.password.request.ForgotPasswordRequest;
import org.kshrd.webblock.user.application.register.response.OtpSentResponse;
import org.kshrd.webblock.user.common.domain.Emails;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.springframework.stereotype.Component;

// Forgot password step 1 (and "Resend code").
// Same answer whether the email has an account or not, so nobody can use this page to find out who has an account.
// The 60 s wait is taken for EVERY email, for the same reason.
@Component
public class ForgotPasswordHandler {

    private final KeycloakAdminClient keycloak;
    private final OtpManager otp;

    public ForgotPasswordHandler(KeycloakAdminClient keycloak, OtpManager otp) {
        this.keycloak = keycloak;
        this.otp = otp;
    }

    public OtpSentResponse handle(ForgotPasswordRequest request) {
        String email = Emails.normalize(request.email());

        otp.startWaitOrRefuse(OtpPurpose.RESET_PASSWORD, email);

        // Only a real, not suspended account gets an email. Everyone gets the same answer.
        keycloak.findUserByEmail(email)
                .filter(KeycloakAdminClient.KeycloakUser::enabled)
                .ifPresent(user -> otp.sendCodeNoWait(OtpPurpose.RESET_PASSWORD, email, ""));

        return new OtpSentResponse(
                otp.settings().codeTtl().toSeconds(),
                otp.settings().resendAfter().toSeconds());
    }
}
