package org.kshrd.webblock.project.common.infrastructure.security;

import java.time.Duration;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtClaimNames;
import org.springframework.security.oauth2.jwt.JwtClaimValidator;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtIssuerValidator;
import org.springframework.security.oauth2.jwt.JwtTimestampValidator;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.SupplierJwtDecoder;
import org.springframework.web.client.RestOperations;
import org.springframework.web.client.RestTemplate;

/**
 * The one JWT decoder: keys from the issuer's discovery document (resolved on first use, so start-up does not need
 * Keycloak), RS256 only, and issuer, audience, expiry, not-before and token type checked. Keycloak writes the type in
 * the {@code typ} claim: {@code Bearer} for access tokens (user and service-account alike), {@code ID} for ID tokens,
 * which an audience mapper could otherwise make acceptable here.
 */
@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(name = "webblock.security.identity-mode", havingValue = "jwt", matchIfMissing = true)
public class JwtDecoderConfig {

    /** Keycloak's token type claim. */
    static final String TOKEN_TYPE_CLAIM = "typ";

    @Bean
    JwtDecoder jwtDecoder(
            @Value("${spring.security.oauth2.resourceserver.jwt.issuer-uri}") String issuer,
            @Value("${spring.security.oauth2.resourceserver.jwt.audiences}") List<String> audiences) {
        return new SupplierJwtDecoder(() -> {
            NimbusJwtDecoder decoder = NimbusJwtDecoder.withIssuerLocation(issuer)
                    .restOperations(restOperations())
                    .jwsAlgorithm(SignatureAlgorithm.RS256)
                    .build();
            JwtTimestampValidator timestamps = new JwtTimestampValidator();
            timestamps.setAllowEmptyExpiryClaim(false);
            JwtClaimValidator<List<String>> audience = new JwtClaimValidator<>(
                    JwtClaimNames.AUD, aud -> aud != null && aud.stream().anyMatch(audiences::contains));
            JwtClaimValidator<String> accessToken = new JwtClaimValidator<>(TOKEN_TYPE_CLAIM, "Bearer"::equals);
            decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<Jwt>(
                    new JwtIssuerValidator(issuer), timestamps, audience, accessToken));
            return decoder;
        });
    }

    /** Discovery and JWKS fetches get short timeouts (constants: connect 2 s, read 5 s) so Keycloak cannot hang a request. */
    private static RestOperations restOperations() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) Duration.ofSeconds(2).toMillis());
        factory.setReadTimeout((int) Duration.ofSeconds(5).toMillis());
        return new RestTemplate(factory);
    }
}
