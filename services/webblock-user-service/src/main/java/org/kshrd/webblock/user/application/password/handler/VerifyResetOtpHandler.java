package org.kshrd.webblock.user.application.password.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.password.request.VerifyResetOtpRequest;
import org.kshrd.webblock.user.application.register.response.VerificationResponse;
import org.kshrd.webblock.user.common.domain.Emails;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.springframework.stereotype.Component;

// Forgot password step 2: check the code; a correct code gives a one-time verification token for step 3
@Component
public class VerifyResetOtpHandler {

    private final OtpManager otp;

    public VerifyResetOtpHandler(OtpManager otp) {
        this.otp = otp;
    }

    public VerificationResponse handle(VerifyResetOtpRequest request) {
        String token = otp.verifyCode(OtpPurpose.RESET_PASSWORD, Emails.normalize(request.email()), request.code());
        return new VerificationResponse(token, otp.settings().verificationTtl().toSeconds());
    }
}
