package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.BadJwtException;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@WebMvcTest(controllers = ProbeController.class)
@Import(WebSliceSupport.Imports.class)
class SecurityErrorWebTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    /** Also Review Focus 4 on the security path: the query is not in instance and not echoed. */
    @Test
    void missingTokenAnswers401WithProblemDetails() throws Exception {
        MvcResult result = mvc.perform(get("/api/test-probe").queryParam("note", "customer-text"))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("WWW-Authenticate", "Bearer"))
                .andExpect(header().string("Content-Type", "application/problem+json"))
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:unauthenticated"))
                .andExpect(jsonPath("$.title").value("Authentication is required."))
                .andExpect(jsonPath("$.status").value(401))
                .andExpect(jsonPath("$.instance").value("/api/test-probe"))
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"))
                .andExpect(jsonPath("$.errors").isArray())
                .andExpect(jsonPath("$.errors").isEmpty())
                .andExpect(content().string(not(containsString("\"detail\""))))
                .andExpect(content().string(not(containsString("\"success\""))))
                .andExpect(content().string(not(containsString("customer-text"))))
                .andReturn();
        String headerId = result.getResponse().getHeader("X-Request-Id");
        assertThat(headerId).isNotBlank();
        assertThat(JsonPath.<String>read(result.getResponse().getContentAsString(), "$.requestId"))
                .isEqualTo(headerId);
    }

    @Test
    void aRefusedTokenAnswers401WithTheInvalidTokenChallenge() throws Exception {
        when(jwtDecoder.decode(anyString())).thenThrow(new BadJwtException("bad signature detail"));
        mvc.perform(get("/api/test-probe").header("Authorization", "Bearer not-a-jwt"))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("WWW-Authenticate", "Bearer error=\"invalid_token\""))
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"))
                .andExpect(content().string(not(containsString("bad signature detail"))));
    }

    @Test
    void aJsonOnlyClientGetsTheSecurityErrorAsJson() throws Exception {
        mvc.perform(get("/api/test-probe").accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("Content-Type", "application/json"))
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:unauthenticated"));
    }

    @Test
    void anAcceptWhoseQualityCannotBeReadGetsTheSecurityErrorAsProblemJson() throws Exception {
        mvc.perform(get("/api/test-probe").header("Accept", "application/json;Q=abc"))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("Content-Type", "application/problem+json"))
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"));
    }

    @Test
    void aTokenWithoutAUserIdAnswers403WithProblemDetails() throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.withoutUserId()))
                .andExpect(status().isForbidden())
                .andExpect(header().doesNotExist("WWW-Authenticate"))
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:access-denied"))
                .andExpect(jsonPath("$.code").value("ACCESS_DENIED"))
                .andExpect(jsonPath("$.status").value(403));
    }

    @Test
    void internalAnswersWithoutAToken() throws Exception {
        mvc.perform(get("/internal/test-probe"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.name").value("internal"));
    }

    @Test
    void unmappedPathsAreDeniedWithProblemDetails() throws Exception {
        mvc.perform(get("/actuator/env"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"))
                .andExpect(jsonPath("$.instance").value("/actuator/env"));
        mvc.perform(get("/foo").with(Users.anyUser()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("ACCESS_DENIED"))
                .andExpect(jsonPath("$.status").value(403))
                .andExpect(jsonPath("$.instance").value("/foo"));
    }
}
