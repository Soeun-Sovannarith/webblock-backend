package org.kshrd.webblock.project.presentation.rest.page;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.model.DeployVersion;
import org.kshrd.webblock.project.application.generation.model.DeployVersionPage;
import org.kshrd.webblock.project.application.page.handler.PageVersionService;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = PageVersionController.class)
@Import(WebSliceSupport.Imports.class)
class PageVersionControllerTest {

    static final UUID OWNER = UUID.fromString("0199e0a2-0000-7000-8000-0000000000aa");
    static final UUID PROJECT = UUID.fromString("0199e0a2-0000-7000-8000-0000000000bb");
    static final UUID VERSION = UUID.fromString("0199e0a2-0000-7000-8000-0000000000dd");
    static final String RESTORE = "/api/v1/projects/{projectId}/versions/{versionId}/restore";

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    PageVersionService versions;

    @Test
    @DisplayName("FR-41.1: the list answers the version items")
    void fr41_1_list() throws Exception {
        when(versions.list(eq(PROJECT), eq(OWNER), eq(2), eq(null)))
                .thenReturn(new DeployVersionPage(
                        List.of(new DeployVersion(VERSION, 3, Instant.parse("2026-10-18T00:00:00Z"), true)), null));

        mvc.perform(get("/api/v1/projects/{projectId}/versions", PROJECT)
                        .param("limit", "2")
                        .with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].versionId").value(VERSION.toString()))
                .andExpect(jsonPath("$.data[0].editRevision").value(3))
                .andExpect(jsonPath("$.data[0].publishedAt").value("2026-10-18T00:00:00Z"))
                .andExpect(jsonPath("$.data[0].current").value(true));
    }

    @Test
    @DisplayName("FR-41.1: a limit out of range is 400")
    void fr41_1_limitOutOfRange() throws Exception {
        mvc.perform(get("/api/v1/projects/{projectId}/versions", PROJECT)
                        .param("limit", "0")
                        .with(Users.user(OWNER)))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(versions);
    }

    @Test
    @DisplayName("FR-41.5: a restore answers the page and the new revision")
    void fr41_5_restore() throws Exception {
        PageRecord page = new PageRecord(
                UUID.randomUUID(),
                PROJECT,
                "Home",
                "/",
                JsonMapper.builder().build().readTree("{\"content\":[],\"root\":{}}"),
                "T",
                null,
                0,
                Instant.parse("2026-10-18T00:00:00Z"),
                Instant.parse("2026-10-18T00:00:00Z"));
        when(versions.restore(eq(PROJECT), eq(VERSION), eq(OWNER), eq(4L))).thenReturn(new PageWrite(page, 5));

        mvc.perform(post(RESTORE, PROJECT, VERSION)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"expectedEditRevision\":4}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.editRevision").value(5))
                .andExpect(jsonPath("$.data.page.path").value("/"))
                .andExpect(jsonPath("$.data.page.seoTitle").value("T"));
    }

    @Test
    @DisplayName("FR-41.5: a missing or negative expectedEditRevision is 400 and nothing is restored")
    void fr41_5_bodyIsValidated() throws Exception {
        for (String body : List.of("{}", "{\"expectedEditRevision\":-1}", "{\"expectedEditRevision\":null}")) {
            mvc.perform(post(RESTORE, PROJECT, VERSION)
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors[0].field").value("expectedEditRevision"));
        }
        verifyNoInteractions(versions);
    }

    @Test
    @DisplayName("FR-41.8: VERSION_NOT_FOUND is a 404 with its own code")
    void fr41_8_versionNotFound() throws Exception {
        when(versions.restore(eq(PROJECT), eq(VERSION), eq(OWNER), eq(0L)))
                .thenThrow(new WebBlockException(ErrorCode.VERSION_NOT_FOUND));

        mvc.perform(post(RESTORE, PROJECT, VERSION)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"expectedEditRevision\":0}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("VERSION_NOT_FOUND"));
    }
}
