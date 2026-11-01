package org.kshrd.webblock.notification.infrastructure.security;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.assertj.core.api.Assertions.assertThat;

class AlertmanagerTokenFilterTest {

    private MockHttpServletResponse call(String configuredToken, String authorizationHeader, MockFilterChain chain)
            throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/v1/alertmanager/alerts");
        if (authorizationHeader != null) {
            request.addHeader("Authorization", authorizationHeader);
        }
        MockHttpServletResponse response = new MockHttpServletResponse();
        new AlertmanagerTokenFilter(configuredToken).doFilter(request, response, chain);
        return response;
    }

    @Test
    @DisplayName("passesTheRightToken")
    void passesRightToken() throws Exception {
        MockFilterChain chain = new MockFilterChain();
        MockHttpServletResponse response = call("s3cret", "Bearer s3cret", chain);

        assertThat(response.getStatus()).isEqualTo(200);
        assertThat(chain.getRequest()).isNotNull();
    }

    @Test
    @DisplayName("refusesAMissingOrWrongToken")
    void refusesMissingOrWrongToken() throws Exception {
        MockFilterChain missing = new MockFilterChain();
        assertThat(call("s3cret", null, missing).getStatus()).isEqualTo(401);
        assertThat(missing.getRequest()).isNull();

        MockFilterChain wrong = new MockFilterChain();
        assertThat(call("s3cret", "Bearer other", wrong).getStatus()).isEqualTo(401);
        assertThat(wrong.getRequest()).isNull();
    }

    @Test
    @DisplayName("refusesEverythingWhenNoTokenIsConfigured")
    void failsClosedWithoutConfiguredToken() throws Exception {
        MockFilterChain chain = new MockFilterChain();
        assertThat(call("", "Bearer ", chain).getStatus()).isEqualTo(401);
        assertThat(chain.getRequest()).isNull();
    }
}
