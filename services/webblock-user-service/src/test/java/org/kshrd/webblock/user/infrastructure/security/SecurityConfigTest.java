package org.kshrd.webblock.user.infrastructure.security;

import org.kshrd.webblock.user.TestcontainersConfiguration;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {"eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test"})
@AutoConfigureMockMvc
@Import(TestcontainersConfiguration.class)
class SecurityConfigTest {

    @Autowired
    MockMvc mockMvc;

    @Autowired
    JwtAuthenticationConverter jwtAuthenticationConverter;

    @Test
    void healthAndSwaggerAreOpen() throws Exception {
        mockMvc.perform(get("/actuator/health")).andExpect(status().isOk());
        mockMvc.perform(get("/v3/api-docs")).andExpect(status().isOk());
    }

    @Test
    void apiNeedsToken() throws Exception {
        mockMvc.perform(get("/api/user/v1/anything")).andExpect(status().isUnauthorized());
    }

    @Test
    void adminPathNeedsAdminRole() throws Exception {
        mockMvc.perform(get("/api/user/v1/admin/anything").with(jwt()))
                .andExpect(status().isForbidden());
        // Allowed through security; 404 only because no controller exists yet
        mockMvc.perform(get("/api/user/v1/admin/anything").with(jwt().authorities(new SimpleGrantedAuthority("ROLE_ADMIN"))))
                .andExpect(status().isNotFound());
    }

    @Test
    void topLevelRolesClaimBecomesSpringRoles() {
        Jwt token = Jwt.withTokenValue("token")
                .header("alg", "RS256")
                .subject("keycloak-user-id")
                .claim("roles", List.of("ADMIN", "USER"))
                .build();

        List<String> roles = jwtAuthenticationConverter.convert(token).getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .toList();

        // Spring Security 7 also adds FACTOR_BEARER (how the user logged in), so check our roles are there
        assertThat(roles).contains("ROLE_ADMIN", "ROLE_USER");
    }

}
