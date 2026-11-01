package org.kshrd.webblock.project.common.infrastructure.security;

import jakarta.servlet.DispatcherType;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.security.autoconfigure.actuate.web.servlet.EndpointRequest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;
import org.springframework.core.annotation.Order;
import org.springframework.security.authorization.AuthorizationDecision;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.server.resource.authentication.AbstractOAuth2TokenAuthenticationToken;
import org.springframework.security.oauth2.server.resource.introspection.OpaqueTokenIntrospector;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.AccessDeniedHandler;

/**
 * The filter chains: actuator, {@code /internal/**} (no authentication), in the prototype profile only the OpenAPI
 * description and Swagger UI (no authentication, TR-40), and everything else ({@code /api/**} for users). The actuator
 * and {@code /api} chains answer 401 and 403 through the one {@link AuthenticationEntryPoint} and
 * {@link AccessDeniedHandler} bean, {@code common.presentation.error.SecurityErrorHandlers}, which writes the Problem
 * Details body. The internal and doc chains permit every request and never answer 401 or 403 themselves.
 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(SecurityProperties.class)
public class SecurityConfig {

    /** Actuator endpoints (served on the management port): health, info and prometheus are open, the rest denied. */
    @Bean
    @Order(1)
    SecurityFilterChain actuatorSecurity(
            HttpSecurity http, AuthenticationEntryPoint entryPoint, AccessDeniedHandler accessDenied) throws Exception {
        http.securityMatcher(EndpointRequest.toAnyEndpoint())
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth.requestMatchers(EndpointRequest.to("health", "info", "prometheus"))
                        .permitAll()
                        .anyRequest()
                        .denyAll())
                .exceptionHandling(ex -> ex.authenticationEntryPoint(entryPoint).accessDeniedHandler(accessDenied));
        return http.build();
    }

    /**
     * Service-to-service calls (decision 0002): no token at all, the mesh's mTLS
     * policy is the gate. No resource server here, so a stale or garbage bearer sent by mistake is never read and can
     * never produce a 401.
     */
    @Bean
    @Order(2)
    SecurityFilterChain internalSecurity(HttpSecurity http) throws Exception {
        http.securityMatcher("/internal/**")
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll());
        return http.build();
    }

    /**
     * TR-40, prototype only: the OpenAPI description and Swagger UI take no token. No resource server here, so a bearer
     * that Swagger UI or a caller sends along is never read. In production this chain does not exist, springdoc serves
     * nothing (ProfileGuard) and the last chain denies these paths.
     */
    @Bean
    @Order(3)
    @Profile("prototype")
    SecurityFilterChain apiDocsSecurity(HttpSecurity http) throws Exception {
        http.securityMatcher("/v3/api-docs", "/v3/api-docs/**", "/swagger-ui.html", "/swagger-ui/**")
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll());
        return http.build();
    }

    @Bean
    @Order(4)
    SecurityFilterChain apiSecurity(
            HttpSecurity http,
            AuthenticationEntryPoint entryPoint,
            AccessDeniedHandler accessDenied,
            SecurityProperties properties,
            @Value("${webblock.security.user-id-claim}") String userIdClaim,
            ObjectProvider<OpaqueTokenIntrospector> devTokens)
            throws Exception {
        http.csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth.dispatcherTypeMatchers(DispatcherType.ERROR)
                        .permitAll()
                        .requestMatchers("/actuator/health/**", "/actuator/info")
                        .permitAll()
                        .requestMatchers("/api/**")
                        .access((authentication, context) ->
                                new AuthorizationDecision(isUser(authentication.get(), userIdClaim)))
                        .anyRequest()
                        .denyAll())
                .oauth2ResourceServer(oauth -> {
                    switch (properties.identityMode()) {
                        case JWT -> oauth.jwt(Customizer.withDefaults());
                        case DEV_TOKENS -> {
                            OpaqueTokenIntrospector introspector = devTokens.getIfAvailable();
                            if (introspector == null) {
                                throw new IllegalStateException(
                                        "webblock.security.identity-mode=dev-tokens needs the prototype profile");
                            }
                            oauth.opaqueToken(opaque -> opaque.introspector(introspector));
                        }
                    }
                    oauth.authenticationEntryPoint(entryPoint).accessDeniedHandler(accessDenied);
                })
                .exceptionHandling(ex -> ex.authenticationEntryPoint(entryPoint).accessDeniedHandler(accessDenied));
        return http.build();
    }

    /** A user: authenticated by a token carrying the user-ID claim as text (otherwise 403). */
    private static boolean isUser(Authentication authentication, String userIdClaim) {
        return authentication instanceof AbstractOAuth2TokenAuthenticationToken<?> token
                && token.isAuthenticated()
                && token.getTokenAttributes().get(userIdClaim) instanceof String;
    }
}
