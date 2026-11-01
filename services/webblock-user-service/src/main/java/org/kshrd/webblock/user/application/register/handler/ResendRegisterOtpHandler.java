package org.kshrd.webblock.user.application.register.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.register.request.ResendOtpRequest;
import org.kshrd.webblock.user.application.register.response.OtpSentResponse;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.Emails;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.springframework.stereotype.Component;

// "Resend code": a new code for a registration that was already started (after the 60 s wait)
@Component
public class ResendRegisterOtpHandler {

    private final OtpManager otp;

    public ResendRegisterOtpHandler(OtpManager otp) {
        this.otp = otp;
    }

    public OtpSentResponse handle(ResendOtpRequest request) {
        String email = Emails.normalize(request.email());
        if (!otp.hasPendingCode(OtpPurpose.REGISTER, email)) {
            // Nothing started, or it expired: the page goes back to step 1
            throw new ApiException(ErrorCode.OTP_EXPIRED);
        }
        otp.sendCode(OtpPurpose.REGISTER, email, otp.pendingFullName(OtpPurpose.REGISTER, email));
        return new OtpSentResponse(
                otp.settings().codeTtl().toSeconds(),
                otp.settings().resendAfter().toSeconds());
    }
}
