package org.kshrd.webblock.project.support;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;

import java.util.UUID;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

/** Authenticated callers for MockMvc: a platform user, or a token that names no user (always refused on /api). */
public final class Users {

    private Users() {}

    public static RequestPostProcessor user(UUID userId) {
        return jwt().jwt(token -> token.claim("user_id", userId.toString()).claim("azp", "webblock-ui"));
    }

    /** Some platform user, for tests that only need an authenticated caller. */
    public static RequestPostProcessor anyUser() {
        return user(UUID.fromString("0199e0a2-0000-7000-8000-0000000000aa"));
    }

    /** A valid token without the user-ID claim, such as a client-credentials token. */
    public static RequestPostProcessor withoutUserId() {
        return jwt().jwt(token -> token.claim("azp", "provisioning"));
    }
}
