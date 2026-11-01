package org.kshrd.webblock.notification.infrastructure.security;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;

/**
 * The signed-in user. Uses {@code jwt.sub}. Open question #10 (auth-tokens.md) may change this to the {@code user_id} claim:
 * when it does, only this class changes.
 * The Knock recipient ids sent by events are {@code account.users.id}, so the bell only shows notifications when sub equals that id.
 */
@Component
public class CurrentUser {

    public String userId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication instanceof JwtAuthenticationToken jwtAuthentication
                && jwtAuthentication.getToken().getSubject() != null) {
            return jwtAuthentication.getToken().getSubject();
        }
        throw new IllegalStateException("No authenticated user in the security context");
    }
}
