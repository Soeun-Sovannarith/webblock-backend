package org.kshrd.webblock.notification.application.notification.response;

import java.time.Instant;

/** Token the browser passes to the Knock in-app feed. Short-lived; never log it. */
public record KnockTokenResponse(String token, Instant expiresAt) {
    @Override
    public String toString() {
        return "KnockTokenResponse[expiresAt=" + expiresAt + "]";
    }
}
