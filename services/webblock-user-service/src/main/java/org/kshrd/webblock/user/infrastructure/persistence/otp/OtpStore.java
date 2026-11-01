package org.kshrd.webblock.user.infrastructure.persistence.otp;

import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.Map;
import java.util.Optional;

// Everything about OTP codes lives in Redis (team decision: no OTP table). Redis deletes keys by itself when they expire.
//
//   otp:<PURPOSE>:<email>          hash  { codeHash, attempts, fullName }   lives codeTtl (10 min)
//   otp-wait:<PURPOSE>:<email>     "1"                                       lives resendAfter (60 s)
//   otp-verified:<tokenHash>       "<PURPOSE>\n<email>\n<fullName>"          lives verificationTtl, used once
//
// Only hashes are stored: never the code itself, never the verification token itself.
@Component
public class OtpStore {

    private static final String CODE_HASH = "codeHash";
    private static final String ATTEMPTS = "attempts";
    private static final String FULL_NAME = "fullName";

    private final StringRedisTemplate redis;

    public OtpStore(StringRedisTemplate redis) {
        this.redis = redis;
    }

    // ---- resend wait ----

    // true = the wait was free and is now taken; false = a code was sent less than resendAfter ago
    public boolean startWait(OtpPurpose purpose, String email, Duration resendAfter) {
        Boolean taken = redis.opsForValue().setIfAbsent(waitKey(purpose, email), "1", resendAfter);
        return Boolean.TRUE.equals(taken);
    }

    // ---- pending code ----

    public void saveCode(OtpPurpose purpose, String email, String codeHash, String fullName, Duration ttl) {
        String key = codeKey(purpose, email);
        redis.delete(key); // a new code always replaces the old one (attempts start again at 0)
        redis.opsForHash().putAll(key, Map.of(
                CODE_HASH, codeHash,
                ATTEMPTS, "0",
                FULL_NAME, fullName == null ? "" : fullName));
        redis.expire(key, ttl);
    }

    public Optional<PendingCode> findCode(OtpPurpose purpose, String email) {
        Map<Object, Object> entries = redis.opsForHash().entries(codeKey(purpose, email));
        if (entries.isEmpty()) {
            return Optional.empty();
        }
        return Optional.of(new PendingCode(
                (String) entries.get(CODE_HASH),
                Integer.parseInt((String) entries.getOrDefault(ATTEMPTS, "0")),
                (String) entries.getOrDefault(FULL_NAME, "")));
    }

    // Adds one wrong try and returns the new total
    public long addWrongAttempt(OtpPurpose purpose, String email) {
        Long attempts = redis.opsForHash().increment(codeKey(purpose, email), ATTEMPTS, 1);
        return attempts == null ? Long.MAX_VALUE : attempts;
    }

    public void deleteCode(OtpPurpose purpose, String email) {
        redis.delete(codeKey(purpose, email));
    }

    // ---- verification token (after a correct code) ----

    public void saveVerification(String tokenHash, OtpPurpose purpose, String email, String fullName, Duration ttl) {
        String value = purpose.name() + "\n" + email + "\n" + (fullName == null ? "" : fullName);
        redis.opsForValue().set(verifiedKey(tokenHash), value, ttl);
    }

    // Reads AND deletes in one step, so a token can never be used twice
    public Optional<Verification> takeVerification(String tokenHash) {
        String value = redis.opsForValue().getAndDelete(verifiedKey(tokenHash));
        if (value == null) {
            return Optional.empty();
        }
        String[] parts = value.split("\n", 3);
        if (parts.length < 3) {
            return Optional.empty();
        }
        return Optional.of(new Verification(OtpPurpose.valueOf(parts[0]), parts[1], parts[2]));
    }

    private static String codeKey(OtpPurpose purpose, String email) {
        return "otp:" + purpose.name() + ":" + email;
    }

    private static String waitKey(OtpPurpose purpose, String email) {
        return "otp-wait:" + purpose.name() + ":" + email;
    }

    private static String verifiedKey(String tokenHash) {
        return "otp-verified:" + tokenHash;
    }

    public record PendingCode(String codeHash, int attempts, String fullName) {
    }

    public record Verification(OtpPurpose purpose, String email, String fullName) {
    }
}
