package org.kshrd.webblock.project.presentation.rest.page;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.application.page.handler.PageRules;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.page.model.PageContent;
import org.kshrd.webblock.project.application.page.model.PageList;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.infrastructure.config.page.PageConfigImport;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultMatcher;
import tools.jackson.core.exc.StreamConstraintsException;
import tools.jackson.core.exc.StreamReadException;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = PageController.class)
@Import({WebSliceSupport.Imports.class, PageConfigImport.class})
class PageControllerTest {

    static final UUID OWNER = UUID.randomUUID();
    static final UUID PROJECT = UUID.randomUUID();
    static final UUID PAGE = UUID.randomUUID();
    static final String TREE = "{\"root\":{\"props\":{}},\"content\":[]}";
    static final Instant T = Instant.parse("2026-10-15T00:00:00Z");

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    PageService pages;

    private static String body(String extra) {
        return "{\"name\":\"Home\",\"path\":\"/\",\"puckTree\":" + TREE + extra + "}";
    }

    private static PageRecord record(UUID pageId, String path, int sortOrder) {
        return new PageRecord(
                pageId,
                PROJECT,
                "Home",
                path,
                JsonMapper.builder().build().readTree(TREE),
                null,
                null,
                sortOrder,
                T,
                T);
    }

    /** A 400 MALFORMED_REQUEST Problem Details body whose cause is Jackson's StreamConstraintsException. */
    private static ResultMatcher[] refusedByStreamConstraints() {
        return new ResultMatcher[] {
            status().isBadRequest(),
            jsonPath("$.success").doesNotExist(),
            jsonPath("$.code").value("MALFORMED_REQUEST"),
            jsonPath("$.status").value(400),
            result -> {
                boolean found = false;
                for (Throwable t = result.getResolvedException(); t != null; t = t.getCause()) {
                    found |= t instanceof StreamConstraintsException;
                }
                assertThat(found).as("caused by StreamConstraintsException").isTrue();
            }
        };
    }

    /**
     * jsonb and text have no U+0000. The application's JSON reader refuses it in every body (JacksonConfig), so the page
     * rules no longer check for it: a NUL in the name, or in a key or a value inside the Puck tree, is a 400 here.
     */
    @ParameterizedTest
    @ValueSource(
            strings = {
                "\"name\":\"Ho\\u0000me\"",
                "\"puckTree\":{\"root\":{\"props\":{\"t\":\"a\\u0000b\"}},\"content\":[]}",
                "\"puckTree\":{\"root\":{\"props\":{\"k\\u0000\":1}},\"content\":[]}"
            })
    void aNulAnywhereInThePageIsA400BeforeTheServiceIsCalled(String field) throws Exception {
        String content = field.startsWith("\"name\"")
                ? "{" + field + ",\"path\":\"/\",\"puckTree\":" + TREE + ",\"expectedEditRevision\":4,\"sortOrder\":0}"
                : "{\"name\":\"Home\",\"path\":\"/\"," + field + ",\"expectedEditRevision\":4,\"sortOrder\":0}";
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(content))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.success").doesNotExist())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"))
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(result -> {
                    boolean found = false;
                    for (Throwable t = result.getResolvedException(); t != null; t = t.getCause()) {
                        found |= t instanceof StreamReadException
                                && String.valueOf(t.getMessage()).contains("U+0000");
                    }
                    assertThat(found).as("caused by the NUL refusal").isTrue();
                });
        verifyNoInteractions(pages);
    }

    @Test
    void listAnswersTheRevisionAndThePages() throws Exception {
        UUID pageId = UUID.randomUUID();
        when(pages.list(PROJECT, OWNER)).thenReturn(new PageList(3, List.of(record(pageId, "/", 0))));
        mvc.perform(get("/api/v1/projects/{p}/pages", PROJECT).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.editRevision").value(3))
                .andExpect(jsonPath("$.data.pages[0].pageId").value(pageId.toString()))
                .andExpect(jsonPath("$.data.pages[0].path").value("/"))
                .andExpect(jsonPath("$.data.pages[0].puckTree.root.props").isMap());
    }

    @Test
    void updateAnswersTheNewRevision() throws Exception {
        UUID pageId = UUID.randomUUID();
        when(pages.update(eq(PROJECT), eq(pageId), eq(OWNER), eq(2L), any(PageContent.class), eq(3)))
                .thenReturn(new PageWrite(record(pageId, "/", 3), 3));
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, pageId)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"expectedEditRevision\":2,\"sortOrder\":3")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.editRevision").value(3))
                .andExpect(jsonPath("$.data.page.sortOrder").value(3));
    }

    @Test
    void updateNeedsTheSortOrder() throws Exception {
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, UUID.randomUUID())
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"expectedEditRevision\":2")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("sortOrder"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        verifyNoInteractions(pages);
    }

    /** L-1: 0 <= sortOrder <= PageRules.MAX_SORT_ORDER (1000). */
    @Test
    void theSortOrderIsBounded() throws Exception {
        for (String bad : new String[] {"-1", "1001", "2147483647"}) {
            mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, UUID.randomUUID())
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body(",\"expectedEditRevision\":2,\"sortOrder\":" + bad)))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.errors[0].field").value("sortOrder"))
                    .andExpect(jsonPath("$.errors[0].code").value("FIELD_OUT_OF_RANGE"));
        }
        verifyNoInteractions(pages);
        UUID pageId = UUID.randomUUID();
        for (int good : new int[] {0, PageRules.MAX_SORT_ORDER}) {
            when(pages.update(eq(PROJECT), eq(pageId), eq(OWNER), eq(2L), any(PageContent.class), eq(good)))
                    .thenReturn(new PageWrite(record(pageId, "/", good), 3));
            mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, pageId)
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body(",\"expectedEditRevision\":2,\"sortOrder\":" + good)))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.data.page.sortOrder").value(good));
        }
    }

    @Test
    void theExpectedRevisionIsRequired() throws Exception {
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"sortOrder\":0")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("expectedEditRevision"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        verifyNoInteractions(pages);
    }

    @Test
    void aBadPathIsAFieldError() throws Exception {
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"expectedEditRevision\":0,\"sortOrder\":0,\"name\":\"x\",\"path\":\"/About Us\","
                                + "\"puckTree\":" + TREE + "}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].code").value("PAGE_PATH_INVALID"));
    }

    /** J1-R10: a missing or JSON-null tree is a field error; the service, and so PuckValidator, never sees it. */
    @Test
    void aMissingOrNullTreeIsAFieldError() throws Exception {
        for (String tree : new String[] {"", ",\"puckTree\":null"}) {
            mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, UUID.randomUUID())
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"expectedEditRevision\":0,\"sortOrder\":0,\"name\":\"x\",\"path\":\"/\"" + tree
                                    + "}"))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.errors[0].field").value("puckTree"))
                    .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        }
        verifyNoInteractions(pages);
    }

    @Test
    void deeplyNestedTreeIsMalformed() throws Exception {
        String nested = "[".repeat(3000) + "]".repeat(3000);
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"expectedEditRevision\":0,\"name\":\"x\",\"path\":\"/\",\"puckTree\":" + nested
                                + "}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
        verifyNoInteractions(pages);
    }

    /** J1-R10: too-deep or too-long input stops Jackson with a StreamConstraintsException, answered as 400. */
    @Test
    void streamConstraintViolationsAreMalformedRequests() throws Exception {
        String deepObjects = "{\"a\":".repeat(1_000) + "1" + "}".repeat(1_000);
        String longNumber = "{\"expectedEditRevision\":" + "9".repeat(1_001) + "}";
        String longName = "{\"" + "n".repeat(50_001) + "\":1}";
        for (String content : new String[] {deepObjects, longNumber, longName}) {
            mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, UUID.randomUUID())
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(content))
                    .andExpectAll(refusedByStreamConstraints());
        }
        verifyNoInteractions(pages);
    }

    @Test
    void aTakenPathIsAConflict() throws Exception {
        when(pages.update(eq(PROJECT), eq(PAGE), eq(OWNER), anyLong(), any(PageContent.class), anyInt()))
                .thenThrow(new DataIntegrityViolationException(
                        "dup",
                        new org.hibernate.exception.ConstraintViolationException(
                                "dup", new SQLException("dup", "23505"), "pages_project_path_key")));
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"expectedEditRevision\":0,\"sortOrder\":0")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("PAGE_PATH_TAKEN"));
    }

    @Test
    void aStaleRevisionIsAConflict() throws Exception {
        when(pages.update(eq(PROJECT), eq(PAGE), eq(OWNER), anyLong(), any(PageContent.class), anyInt()))
                .thenThrow(new WebBlockException(ErrorCode.STALE_EDIT_REVISION));
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"expectedEditRevision\":0,\"sortOrder\":0")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("STALE_EDIT_REVISION"));
    }

    @Test
    void aNegativeExpectedRevisionIsOutOfRange() throws Exception {
        mvc.perform(put("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"expectedEditRevision\":-1,\"sortOrder\":0")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_OUT_OF_RANGE"));
        verifyNoInteractions(pages);
    }

    /** The owner's decision: a website has one page, so a page is neither added nor deleted. */
    @Test
    @DisplayName("FR-18: POST /projects/{id}/pages is gone")
    void fr18_addingAPageNoLongerRoutes() throws Exception {
        mvc.perform(post("/api/v1/projects/{p}/pages", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(",\"expectedEditRevision\":0")))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(jsonPath("$.status").value(405));
        verifyNoInteractions(pages);
    }

    @Test
    @DisplayName("FR-18: DELETE /projects/{id}/pages/{pageId} is gone")
    void fr18_deletingAPageNoLongerRoutes() throws Exception {
        mvc.perform(delete("/api/v1/projects/{p}/pages/{id}", PROJECT, PAGE)
                        .param("expectedEditRevision", "7")
                        .with(Users.user(OWNER)))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(jsonPath("$.status").value(405));
        verifyNoInteractions(pages);
    }

    @Test
    void anAnonymousCallerIsUnauthenticated() throws Exception {
        mvc.perform(get("/api/v1/projects/{p}/pages", PROJECT)).andExpect(status().isUnauthorized());
        verifyNoInteractions(pages);
    }
}
