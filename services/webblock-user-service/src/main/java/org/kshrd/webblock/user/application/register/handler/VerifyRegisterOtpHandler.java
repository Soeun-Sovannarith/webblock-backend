package org.kshrd.webblock.user.application.register.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.register.request.VerifyOtpRequest;
import org.kshrd.webblock.user.application.register.response.VerificationResponse;
import org.kshrd.webblock.user.common.domain.Emails;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.springframework.stereotype.Component;

// Register step 2: check the code; a correct code gives a one-time verification token for step 3
@Component
public class VerifyRegisterOtpHandler {

    private final OtpManager otp;

    public VerifyRegisterOtpHandler(OtpManager otp) {
        this.otp = otp;
    }

    public VerificationResponse handle(VerifyOtpRequest request) {
        String token = otp.verifyCode(OtpPurpose.REGISTER, Emails.normalize(request.email()), request.code());
        return new VerificationResponse(token, otp.settings().verificationTtl().toSeconds());
    }
}
