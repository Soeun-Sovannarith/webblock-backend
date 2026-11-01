package org.kshrd.webblock.project.common.infrastructure.security;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.net.URI;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Pins Spring Security's default request firewall, which answers 400 before any chain is chosen: no path trick can
 * cross between {@code /api} (authenticated) and {@code /internal} (no authentication). A future {@code HttpFirewall}
 * bean that relaxes one of these fails here. The paths are sent raw, not template-encoded.
 */
@WebMvcTest(controllers = SecurityProbeController.class)
@Import(WebSliceSupport.Imports.class)
class RequestFirewallWebTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @ParameterizedTest
    @ValueSource(
            strings = {
                "/api/../internal/test-security",
                "/internal/../api/test-security",
                "/api/%2e%2e/internal/test-security",
                "/internal/%2e%2e/api/test-security",
                "/api%2Ftest-security",
                "/internal%2Ftest-security",
                "/api/test-security;x=1",
                "/internal/test-security;x=1",
                "//api/test-security",
                "//internal/test-security",
                "/api/test-security%00",
                "/internal/test-security%00"
            })
    void aPathTrickIsRejectedBeforeAnyChain(String uri) throws Exception {
        mvc.perform(get(raw(uri))).andExpect(status().isBadRequest());
    }

    /** The control: the same paths without the trick are served by their chains (so a 400 above is the firewall's). */
    @Test
    void thePlainPathsReachTheirChains() throws Exception {
        mvc.perform(get(raw("/internal/test-security"))).andExpect(status().isOk());
        mvc.perform(get(raw("/api/test-security"))).andExpect(status().isUnauthorized());
    }

    /** With an authority, so that a leading {@code //} stays in the path instead of naming a host. */
    private static URI raw(String path) {
        return URI.create("http://localhost" + path);
    }
}
