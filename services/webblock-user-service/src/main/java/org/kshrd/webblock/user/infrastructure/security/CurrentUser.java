package org.kshrd.webblock.user.infrastructure.security;

import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.springframework.security.oauth2.jwt.Jwt;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.regex.Pattern;

// Who is calling, read from the verified Keycloak access token (team format, see webblock-commons):
// "user_id" = our account id (= Keycloak user id), "roles" = USER / ADMIN.
// All token-claim names live in THIS file only.
public record CurrentUser(UUID userId, String issuer, String subject, String email, String fullName,
                          Instant signedInAt, List<String> roles, String sessionId) {

    public static final String ADMIN = "ADMIN";

    // Same rule as webblock-commons: lower case, canonical, not the nil UUID
    private static final Pattern CANONICAL_UUID =
            Pattern.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");

    public static CurrentUser from(Jwt jwt) {
        String userId = jwt.getClaimAsString("user_id");
        if (userId == null || !CANONICAL_UUID.matcher(userId).matches()
                || userId.equals("00000000-0000-0000-0000-000000000000")) {
            // Signed in, but the token has no valid user_id: same answer as the team library (403)
            throw new ApiException(ErrorCode.ACCESS_DENIED);
        }
        List<String> roles = jwt.getClaimAsStringList("roles");
        return new CurrentUser(
                UUID.fromString(userId),
                jwt.getClaimAsString("iss"),
                jwt.getSubject(),
                jwt.getClaimAsString("email"),
                jwt.getClaimAsString("name"),        // Keycloak "name" = first + last name
                jwt.getClaimAsInstant("auth_time"),  // when the user really signed in
                roles == null ? List.of() : List.copyOf(roles),
                jwt.getClaimAsString("sid"));        // Keycloak session: kept when "sign out other devices"
    }

    public boolean isAdmin() {
        return roles.contains(ADMIN);
    }

    // Keycloak user id as text (same value as userId, team decision)
    public String keycloakUserId() {
        return userId.toString();
    }
}
