package org.kshrd.webblock.project.common.presentation.filter;

import static org.hamcrest.Matchers.matchesPattern;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.common.presentation.error.ProbeController;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = ProbeController.class)
@Import(WebSliceSupport.Imports.class)
class RequestIdWebTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @Test
    void echoesASafeIncomingId() throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.anyUser()).header("X-Request-Id", "abc-123.x_y"))
                .andExpect(header().string("X-Request-Id", "abc-123.x_y"))
                .andExpect(jsonPath("$.meta.requestId").value("abc-123.x_y"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"has spaces", "semi;colon", "line\u0001ctl"})
    void replacesAnUnsafeIncomingId(String unsafe) throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.anyUser()).header("X-Request-Id", unsafe))
                .andExpect(header().string("X-Request-Id", matchesPattern(WebTestSupport.UUID_V7)))
                .andExpect(jsonPath("$.meta.requestId").value(matchesPattern(WebTestSupport.UUID_V7)));
    }

    @Test
    void replacesAnIdLongerThan128Characters() throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.anyUser()).header("X-Request-Id", "a".repeat(129)))
                .andExpect(header().string("X-Request-Id", matchesPattern(WebTestSupport.UUID_V7)));
    }
}
