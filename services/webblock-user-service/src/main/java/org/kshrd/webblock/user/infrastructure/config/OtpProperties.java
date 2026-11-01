package org.kshrd.webblock.user.infrastructure.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

// webblock.otp.* in application.yml. Values that "can change" live here, not in code.
@ConfigurationProperties(prefix = "webblock.otp")
public record OtpProperties(
        String secret,              // server secret used to hash codes (OTP_SECRET); never commit it
        Duration codeTtl,           // how long a code works (10 minutes)
        int maxAttempts,            // wrong tries before the code is deleted (5)
        Duration resendAfter,       // wait before a new code can be sent (60 seconds)
        Duration verificationTtl) { // how long the verification token works (10 minutes)
}
