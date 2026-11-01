package org.kshrd.webblock.notification.infrastructure.security;

import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.intercept.AuthorizationFilter;

import java.util.Collection;
import java.util.List;
import java.util.Map;

// This service checks the Keycloak JWT itself (signature + issuer), no call to user-service.
// Keycloak realm roles (realm_access.roles) become ROLE_<role>, so use @PreAuthorize("hasRole('admin')").
@Configuration
@EnableMethodSecurity
public class SecurityConfig {

    // Alertmanager webhook: Alertmanager has no Keycloak user, so it sends a shared-secret token instead of a JWT.
    // Checked first; every other path falls through to the JWT chain below.
    @Bean
    @Order(1)
    SecurityFilterChain alertmanagerFilterChain(HttpSecurity http, NotificationProperties properties) throws Exception {
        http
                .securityMatcher("/internal/v1/alertmanager/**")
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .addFilterBefore(new AlertmanagerTokenFilter(properties.alertmanager().webhookToken()), AuthorizationFilter.class)
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll());
        return http.build();
    }

    @Bean
    SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health", "/actuator/health/**").permitAll()
                        // Prometheus scrapes without a token. Not under /api/notification/v1, so the gateway and Istio routes do not publish it.
                        .requestMatchers("/actuator/prometheus").permitAll()
                        .requestMatchers("/v3/api-docs/**", "/swagger-ui/**", "/swagger-ui.html").permitAll()
                        .requestMatchers("/api/notification/v1/admin/**").hasRole("admin")
                        .anyRequest().authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2.jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthenticationConverter())));
        return http.build();
    }

    @Bean
    JwtAuthenticationConverter jwtAuthenticationConverter() {
        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(jwt -> {
            Map<String, Object> realmAccess = jwt.getClaimAsMap("realm_access");
            if (realmAccess == null || !(realmAccess.get("roles") instanceof Collection<?> roles)) {
                return List.of();
            }
            return roles.stream()
                    .map(role -> (GrantedAuthority) new SimpleGrantedAuthority("ROLE_" + role))
                    .toList();
        });
        return converter;
    }

}
