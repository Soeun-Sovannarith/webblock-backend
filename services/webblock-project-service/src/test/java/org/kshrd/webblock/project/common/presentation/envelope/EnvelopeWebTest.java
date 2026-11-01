package org.kshrd.webblock.project.common.presentation.envelope;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.matchesPattern;
import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.presentation.error.ProbeController;
import org.kshrd.webblock.project.common.presentation.filter.WebTestSupport;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = ProbeController.class)
@Import(WebSliceSupport.Imports.class)
class EnvelopeWebTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @Test
    void wrapsACreatedResponseWithItsStatusAndRequestId() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"shop\",\"path\":\"/a\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.name").value("shop"))
                .andExpect(content().string(not(containsString("\"error\""))))
                .andExpect(jsonPath("$.meta.status").value(201))
                .andExpect(jsonPath("$.meta.path").value("/api/test-probe"))
                .andExpect(jsonPath("$.meta.requestId").value(matchesPattern(WebTestSupport.UUID_V7)))
                .andExpect(jsonPath("$.meta.timestamp").isString())
                .andExpect(jsonPath("$.meta.page").value(nullValue()))
                .andExpect(header().string("X-Request-Id", matchesPattern(WebTestSupport.UUID_V7)));
    }

    @Test
    void wrapsAPlainObjectWithStatus200() throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.name").value("probe"))
                .andExpect(jsonPath("$.meta.status").value(200));
    }

    @Test
    void listsPutItemsInDataAndPagingInMeta() throws Exception {
        mvc.perform(get("/api/test-probe/list").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].name").value("a"))
                .andExpect(jsonPath("$.meta.page.limit").value(20))
                .andExpect(jsonPath("$.meta.page.nextCursor").value("c1"));
    }

    @Test
    void usesCamelCaseFieldNames() throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.anyUser()))
                .andExpect(jsonPath("$.meta.requestId").exists())
                .andExpect(jsonPath("$.meta.request_id").doesNotExist());
    }

    @Test
    void doesNotWrapAnErrorStatusEntityAsSuccess() throws Exception {
        mvc.perform(get("/api/test-probe/error-entity").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.success").doesNotExist())
                .andExpect(jsonPath("$.meta").doesNotExist());
    }

    @Test
    void doesNotWrapAProblemDetail() throws Exception {
        mvc.perform(get("/api/test-probe/problem").with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.success").doesNotExist())
                .andExpect(jsonPath("$.detail").value("problem"));
    }

    @Test
    void passesStringBytesAndResourceBodiesThroughUnwrapped() throws Exception {
        mvc.perform(get("/api/test-probe/string").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(content().string("plain"));
        mvc.perform(get("/api/test-probe/bytes").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(content().bytes(new byte[] {1, 2, 3}));
        mvc.perform(get("/api/test-probe/resource").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(content().bytes(new byte[] {4, 5}));
    }
}
