package org.kshrd.webblock.user.application.register.response;

import java.util.UUID;

// Account created (or password added to an existing Google account). Next: Sign in.
public record RegisteredResponse(UUID userId, String email) {
}
