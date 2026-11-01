package org.kshrd.webblock.user.infrastructure.security;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.web.BearerTokenResolver;
import org.springframework.security.oauth2.server.resource.web.DefaultBearerTokenResolver;
import org.springframework.security.web.SecurityFilterChain;

import java.util.List;

// This service checks the Keycloak JWT itself (signature, issuer, audience "webblock-account" from application.yml).
// Token format = the team's webblock-commons library: top-level "roles" claim with USER / ADMIN
// (never realm_access). They become ROLE_USER / ROLE_ADMIN, so use hasRole("ADMIN").
@Configuration
@EnableMethodSecurity
public class SecurityConfig {

    // Sign-up and forgot-password endpoints: the person has no account yet, or cannot sign in, so no token
    static final String PUBLIC_REGISTER = "/api/user/v1/register/";
    static final String PUBLIC_PASSWORD = "/api/user/v1/password/";

    @Bean
    SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health", "/actuator/health/**").permitAll()
                        .requestMatchers("/v3/api-docs/**", "/swagger-ui/**", "/swagger-ui.html").permitAll()
                        .requestMatchers(PUBLIC_REGISTER + "**", PUBLIC_PASSWORD + "**").permitAll()
                        .requestMatchers("/api/user/v1/admin/**").hasRole("ADMIN")
                        .anyRequest().authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2
                        .bearerTokenResolver(bearerTokenResolver())
                        .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthenticationConverter())));
        return http.build();
    }

    // On public paths, ignore any Authorization header (an old, expired token must not turn sign-up into a 401)
    @Bean
    BearerTokenResolver bearerTokenResolver() {
        DefaultBearerTokenResolver normal = new DefaultBearerTokenResolver();
        return request -> {
            String path = request.getRequestURI();
            boolean isPublic = path.startsWith(PUBLIC_REGISTER) || path.startsWith(PUBLIC_PASSWORD);
            return isPublic ? null : normal.resolve(request);
        };
    }

    @Bean
    JwtAuthenticationConverter jwtAuthenticationConverter() {
        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(jwt -> {
            List<String> roles = jwt.getClaimAsStringList("roles");
            if (roles == null) {
                return List.of();
            }
            return roles.stream()
                    .map(role -> (GrantedAuthority) new SimpleGrantedAuthority("ROLE_" + role))
                    .toList();
        });
        return converter;
    }

}
