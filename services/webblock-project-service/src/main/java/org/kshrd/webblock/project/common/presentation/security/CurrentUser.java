package org.kshrd.webblock.project.common.presentation.security;

import java.util.UUID;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.AbstractOAuth2TokenAuthenticationToken;
import org.springframework.stereotype.Component;

/**
 * The calling platform user: the account.users ID in the token claim {@code webblock.security.user-id-claim}
 * (decision 0002). Works for JWTs and for the prototype's dev tokens alike.
 */
@Component
public class CurrentUser {

    private final String claim;

    public CurrentUser(@Value("${webblock.security.user-id-claim}") String claim) {
        this.claim = claim;
    }

    public UUID userId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication instanceof AbstractOAuth2TokenAuthenticationToken<?> token
                && token.getTokenAttributes().get(claim) instanceof String text) {
            try {
                UUID id = UUID.fromString(text);
                // Canonical lowercase form only (UUID.fromString also accepts "1-1-1-1-1"), and never the nil UUID.
                if (text.equals(id.toString()) && (id.getMostSignificantBits() | id.getLeastSignificantBits()) != 0) {
                    return id;
                }
            } catch (IllegalArgumentException e) {
                // not a UUID: refused below
            }
        }
        throw new WebBlockException(ErrorCode.ACCESS_DENIED);
    }
}
