package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.response.KnockTokenResponse;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.kshrd.webblock.notification.infrastructure.security.KnockTokenSigner;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.Instant;

/** Issues a Knock user token for the signed-in user. The user id comes from the verified JWT, never from the request. */
@Service
public class IssueKnockTokenHandler {

    private final KnockTokenSigner signer;
    private final NotificationProperties properties;
    private final Clock clock = Clock.systemUTC();

    public IssueKnockTokenHandler(KnockTokenSigner signer, NotificationProperties properties) {
        this.signer = signer;
        this.properties = properties;
    }

    /** @return the token, or null when the signing key is not configured */
    public KnockTokenResponse issue(String userId) {
        if (!signer.isConfigured()) {
            return null;
        }
        Instant now = clock.instant();
        Instant expiresAt = now.plus(properties.knock().tokenTtl());
        return new KnockTokenResponse(signer.sign(userId, now, expiresAt), expiresAt);
    }
}
