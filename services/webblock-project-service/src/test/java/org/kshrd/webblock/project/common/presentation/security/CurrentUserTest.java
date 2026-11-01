package org.kshrd.webblock.project.common.presentation.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;

class CurrentUserTest {

    private final CurrentUser currentUser = new CurrentUser("user_id");

    @AfterEach
    void clear() {
        SecurityContextHolder.clearContext();
    }

    private static void authenticate(Map<String, Object> claims) {
        Jwt jwt = new Jwt("t", Instant.now(), Instant.now().plusSeconds(60), Map.of("alg", "RS256"), claims);
        SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(jwt, List.of()));
    }

    @Test
    void readsTheConfiguredClaim() {
        UUID id = UUID.fromString("0199e0a2-0000-7000-8000-00000000000a");
        authenticate(Map.of("sub", "s", "user_id", id.toString()));
        assertThat(currentUser.userId()).isEqualTo(id);
    }

    @Test
    void refusesAMissingClaim() {
        authenticate(Map.of("sub", "s"));
        assertThatThrownBy(currentUser::userId)
                .isInstanceOfSatisfying(
                        WebBlockException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.ACCESS_DENIED));
    }

    @Test
    void refusesNonCanonicalNilAndNonStringValues() {
        for (Object bad : List.of(
                "1-1-1-1-1", "00000000-0000-0000-0000-000000000000", "0199E0A2-0000-7000-8000-00000000000A", 42)) {
            authenticate(Map.of("sub", "s", "user_id", bad));
            assertThatThrownBy(currentUser::userId).as(String.valueOf(bad)).isInstanceOf(WebBlockException.class);
        }
    }

    @Test
    void refusesAnAnonymousCaller() {
        assertThatThrownBy(currentUser::userId).isInstanceOf(WebBlockException.class);
    }
}
