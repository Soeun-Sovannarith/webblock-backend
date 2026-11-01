package org.kshrd.webblock.project.common.infrastructure.security;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.nimbusds.jwt.JWTClaimsSet;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.LocalIssuer;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

@WebMvcTest(controllers = SecurityProbeController.class)
@Import({WebSliceSupport.Imports.class, JwtDecoderConfig.class})
class JwtSecurityWebTest {

    static final LocalIssuer ISSUER = new LocalIssuer();
    static final UUID USER = UUID.fromString("0199e0a2-0000-7000-8000-000000000001");
    static final String OPEN = "/api/test-security/open";

    @DynamicPropertySource
    static void issuer(DynamicPropertyRegistry registry) {
        registry.add("spring.security.oauth2.resourceserver.jwt.issuer-uri", ISSUER::issuer);
        registry.add("spring.security.oauth2.resourceserver.jwt.audiences", () -> "project");
    }

    @AfterAll
    static void stop() {
        ISSUER.close();
    }

    @Autowired
    MockMvc mvc;

    private ResultActions call(String path, String token) throws Exception {
        return mvc.perform(get(path).header("Authorization", "Bearer " + token));
    }

    private JWTClaimsSet.Builder user() {
        return ISSUER.claims().claim("azp", "webblock-ui").claim("user_id", USER.toString());
    }

    @Test
    void internalIsReachedWithoutAToken() throws Exception {
        mvc.perform(get("/internal/test-security"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.reached").value("internal"));
    }

    /** /internal has no resource server: a bearer sent by mistake is never read, so it can never cause a 401. */
    @Test
    void aGarbageBearerOnInternalIsIgnored() throws Exception {
        call("/internal/test-security", "not-a-jwt").andExpect(status().isOk());
    }

    @Test
    void anExpiredBearerOnInternalIsIgnored() throws Exception {
        Instant past = Instant.now().minusSeconds(3600);
        call(
                        "/internal/test-security",
                        ISSUER.token(user().issueTime(Date.from(past.minusSeconds(60)))
                                .expirationTime(Date.from(past))
                                .build()))
                .andExpect(status().isOk());
    }

    @Test
    void userTokenReachesTheApiWithItsUserId() throws Exception {
        call("/api/test-security", ISSUER.token(user().build()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.userId").value(USER.toString()));
    }

    /**
     * The 403s below come from the chain itself: {@code /api/test-security/open} never reads {@link
     * org.kshrd.webblock.project.common.presentation.security.CurrentUser}, which would refuse such a token on its own,
     * like the catalog handlers that never read it either.
     */
    @Test
    void theOpenProbeIsReachedByAUserToken() throws Exception {
        call(OPEN, ISSUER.token(user().build()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.reached").value("api"));
    }

    @Test
    void aTokenWithoutAUserIdIsForbiddenByTheChain() throws Exception {
        call(OPEN, ISSUER.token(ISSUER.claims().claim("azp", "provisioning").build()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("ACCESS_DENIED"));
    }

    @Test
    void aUserIdThatIsNotTextIsForbiddenByTheChain() throws Exception {
        call(OPEN, ISSUER.token(user().claim("user_id", 42).build()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("ACCESS_DENIED"));
    }

    /**
     * Keycloak marks its token types in {@code typ}: an ID token whose audience includes {@code project} (an audience
     * mapper added to ID tokens) is still not an access token.
     */
    @Test
    void anIdTokenIsUnauthenticated() throws Exception {
        call("/api/test-security", ISSUER.token(user().claim("typ", "ID").build()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"));
    }

    @Test
    void aTokenWithoutATypeIsUnauthenticated() throws Exception {
        call("/api/test-security", ISSUER.token(user().claim("typ", null).build()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void noTokenOnTheApiIsUnauthenticated() throws Exception {
        mvc.perform(get("/api/test-security")).andExpect(status().isUnauthorized());
    }

    @Test
    void wrongAudienceIsUnauthenticated() throws Exception {
        call(
                        "/api/test-security",
                        ISSUER.token(user().audience(List.of("payment")).build()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"));
    }

    @Test
    void wrongIssuerIsUnauthenticated() throws Exception {
        call(
                        "/api/test-security",
                        ISSUER.token(user().issuer("http://127.0.0.1:1/evil").build()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void expiredTokenIsUnauthenticated() throws Exception {
        Instant past = Instant.now().minusSeconds(3600);
        call(
                        "/api/test-security",
                        ISSUER.token(user().issueTime(Date.from(past.minusSeconds(60)))
                                .expirationTime(Date.from(past))
                                .build()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void unsignedTokenIsUnauthenticated() throws Exception {
        call("/api/test-security", ISSUER.unsignedToken(user().build())).andExpect(status().isUnauthorized());
    }

    @Test
    void tokenSignedWithAForeignKeyIsUnauthenticated() throws Exception {
        call("/api/test-security", ISSUER.foreignToken(user().build())).andExpect(status().isUnauthorized());
    }

    @Test
    void tokenWithoutExpiryIsUnauthenticated() throws Exception {
        call("/api/test-security", ISSUER.token(user().expirationTime(null).build()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void hmacTokenSignedWithThePublicKeyBytesIsUnauthenticated() throws Exception {
        call("/api/test-security", ISSUER.hmacTokenWithPublicKeyAsSecret(user().build()))
                .andExpect(status().isUnauthorized());
    }
}
