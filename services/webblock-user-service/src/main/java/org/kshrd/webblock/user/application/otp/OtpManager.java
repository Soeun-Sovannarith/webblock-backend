package org.kshrd.webblock.user.application.otp;

import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.kshrd.webblock.user.infrastructure.client.mail.OtpMailSender;
import org.kshrd.webblock.user.infrastructure.config.OtpProperties;
import org.kshrd.webblock.user.infrastructure.persistence.otp.OtpStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;

// The 6-digit code rules, shared by Register and (later) Forgot password:
// random code, only a hash is stored, expires, max wrong tries, wait before resend, one-time verification token.
@Component
public class OtpManager {

    private static final Logger log = LoggerFactory.getLogger(OtpManager.class);
    private static final SecureRandom RANDOM = new SecureRandom();

    private final OtpStore store;
    private final OtpMailSender mailSender;
    private final OtpProperties props;

    public OtpManager(OtpStore store, OtpMailSender mailSender, OtpProperties props) {
        this.store = store;
        this.mailSender = mailSender;
        this.props = props;
    }

    // Makes a new code, saves its hash and emails it. Refuses if the last code was sent less than resendAfter ago.
    public void sendCode(OtpPurpose purpose, String email, String fullName) {
        startWaitOrRefuse(purpose, email);
        sendCodeNoWait(purpose, email, fullName);
    }

    // Takes the resend wait for this email. Refuses with OTP_RESEND_TOO_SOON if it is already taken.
    public void startWaitOrRefuse(OtpPurpose purpose, String email) {
        if (!store.startWait(purpose, email, props.resendAfter())) {
            throw new ApiException(ErrorCode.OTP_RESEND_TOO_SOON);
        }
    }

    // Makes, saves and emails a code. Call startWaitOrRefuse first.
    public void sendCodeNoWait(OtpPurpose purpose, String email, String fullName) {
        String code = "%06d".formatted(RANDOM.nextInt(1_000_000));
        store.saveCode(purpose, email, hashOtp(purpose, email, code), fullName, props.codeTtl());
        mailSender.send(email, code, purpose, props.codeTtl());
    }

    // The full name saved with the pending code (empty when there is no pending code)
    public String pendingFullName(OtpPurpose purpose, String email) {
        return store.findCode(purpose, email).map(OtpStore.PendingCode::fullName).orElse("");
    }

    public boolean hasPendingCode(OtpPurpose purpose, String email) {
        return store.findCode(purpose, email).isPresent();
    }

    // Checks the code. Right code: deletes it and returns a one-time verification token. Wrong code: counts a try.
    public String verifyCode(OtpPurpose purpose, String email, String code) {
        OtpStore.PendingCode pending = store.findCode(purpose, email)
                .orElseThrow(() -> new ApiException(ErrorCode.OTP_EXPIRED));

        if (pending.attempts() >= props.maxAttempts()) {
            store.deleteCode(purpose, email);
            throw new ApiException(ErrorCode.OTP_TOO_MANY_ATTEMPTS);
        }

        boolean same = MessageDigest.isEqual(
                pending.codeHash().getBytes(StandardCharsets.UTF_8),
                hashOtp(purpose, email, code).getBytes(StandardCharsets.UTF_8));

        if (!same) {
            long attempts = store.addWrongAttempt(purpose, email);
            if (attempts >= props.maxAttempts()) {
                store.deleteCode(purpose, email);
                throw new ApiException(ErrorCode.OTP_TOO_MANY_ATTEMPTS);
            }
            throw new ApiException(ErrorCode.OTP_INVALID);
        }

        store.deleteCode(purpose, email); // a code works only once
        String token = newVerificationToken();
        store.saveVerification(sha256(token), purpose, email, pending.fullName(), props.verificationTtl());
        log.info("OTP verified, purpose={}", purpose);
        return token;
    }

    // Uses up a verification token (works once). Wrong, expired or other-purpose token: refused.
    public OtpStore.Verification takeVerification(OtpPurpose purpose, String token) {
        OtpStore.Verification verification = store.takeVerification(sha256(token))
                .orElseThrow(() -> new ApiException(ErrorCode.VERIFICATION_TOKEN_INVALID));
        if (verification.purpose() != purpose) {
            throw new ApiException(ErrorCode.VERIFICATION_TOKEN_INVALID);
        }
        return verification;
    }

    public OtpProperties settings() {
        return props;
    }

    // ---- hashing ----

    // HMAC with a server secret: even with Redis in hand, nobody can check the 1,000,000 codes offline
    private String hashOtp(OtpPurpose purpose, String email, String code) {
        if (props.secret() == null || props.secret().isBlank()) {
            log.error("OTP_SECRET is not set");
            throw new ApiException(ErrorCode.INTERNAL_ERROR);
        }
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(props.secret().getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] result = mac.doFinal((purpose.name() + "|" + email + "|" + code).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(result);
        } catch (Exception e) {
            throw new IllegalStateException("Cannot hash OTP", e);
        }
    }

    private static String newVerificationToken() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
