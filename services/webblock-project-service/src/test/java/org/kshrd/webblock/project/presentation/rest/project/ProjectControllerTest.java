package org.kshrd.webblock.project.presentation.rest.project;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.project.handler.ProjectLifecycleService;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.handler.ProjectViewAssembler;
import org.kshrd.webblock.project.application.project.model.ProjectPage;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.project.model.ProjectView;
import org.kshrd.webblock.project.application.project.response.ProjectCounts;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = ProjectController.class)
@Import(WebSliceSupport.Imports.class)
class ProjectControllerTest {

    static final UUID OWNER = UUID.fromString("0199e0a2-0000-7000-8000-0000000000aa");

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    ProjectService projects;

    @MockitoBean
    ProjectLifecycleService lifecycle;

    @MockitoBean
    ProjectViewAssembler views;

    @BeforeEach
    void plainViews() {
        when(views.view(any())).thenAnswer(call -> ProjectView.of(call.getArgument(0)));
        when(views.views(any()))
                .thenAnswer(call -> call.<List<ProjectRecord>>getArgument(0).stream()
                        .map(ProjectView::of)
                        .toList());
    }

    private static ProjectRecord record(UUID id, String name) {
        return record(id, name, ProjectStatus.draft);
    }

    private static ProjectRecord record(UUID id, String name, ProjectStatus status) {
        Instant t = Instant.parse("2026-10-15T00:00:00Z");
        return new ProjectRecord(
                id,
                OWNER,
                name,
                "kilncoffee",
                UUID.randomUUID(),
                3,
                "0.1.0",
                status,
                "kilncoffee.sites.webblock.test",
                t,
                t,
                null);
    }

    @Test
    @DisplayName("FR-15.1: subdomain in the read; a suspended website is 200, readOnly, no ownerUserId")
    void fr15_1_theReadCarriesTheSubdomain() throws Exception {
        UUID id = UUID.randomUUID();
        when(projects.getOwned(id, OWNER)).thenReturn(record(id, "Kiln Coffee", ProjectStatus.suspended));
        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.projectId").value(id.toString()))
                .andExpect(jsonPath("$.data.subdomain").value("kilncoffee"))
                .andExpect(jsonPath("$.data.platformHostname").value("kilncoffee.sites.webblock.test"))
                .andExpect(jsonPath("$.data.readOnly").value(true))
                .andExpect(jsonPath("$.data.ownerUserId").doesNotExist());
    }

    @Test
    void listsTheCallersProjectsWithPagingInMeta() throws Exception {
        UUID id = UUID.randomUUID();
        when(projects.listOwned(OWNER, 20, null, null, null))
                .thenReturn(new ProjectPage(List.of(record(id, "Shop")), "next"));
        mvc.perform(get("/api/v1/projects").with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].projectId").value(id.toString()))
                .andExpect(jsonPath("$.data[0].status").value("draft"))
                .andExpect(jsonPath("$.data[0].readOnly").value(false))
                .andExpect(jsonPath("$.data[0].ownerUserId").doesNotExist())
                .andExpect(jsonPath("$.meta.page.nextCursor").value("next"));
    }

    @Test
    @DisplayName("FR-14.5: q and status are passed to the service with the page parameters")
    void fr14_5_searchAndStatusReachTheService() throws Exception {
        UUID id = UUID.randomUUID();
        when(projects.listOwned(OWNER, 5, "abc", "kiln", "active"))
                .thenReturn(new ProjectPage(List.of(record(id, "Kiln")), null));
        mvc.perform(get("/api/v1/projects")
                        .param("limit", "5")
                        .param("cursor", "abc")
                        .param("q", "kiln")
                        .param("status", "active")
                        .with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].projectId").value(id.toString()))
                .andExpect(jsonPath("$.data[0].hasUndeployedChanges").value(true))
                .andExpect(jsonPath("$.data[0].lastDeployedEditRevision").value(org.hamcrest.Matchers.nullValue()));
    }

    @Test
    @DisplayName("FR-14.6: a q over 100 characters is 400 VALIDATION_FAILED (an unknown status: FR-14.6 service tests)")
    void fr14_6_badFiltersAreRefusedBeforeTheService() throws Exception {
        mvc.perform(get("/api/v1/projects").param("q", "x".repeat(101)).with(Users.user(OWNER)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        verifyNoInteractions(projects);
    }

    @Test
    void aLimitOutOfRangeIsRefusedBeforeTheService() throws Exception {
        mvc.perform(get("/api/v1/projects").param("limit", "0").with(Users.user(OWNER)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        verifyNoInteractions(projects);
    }

    @Test
    void someoneElsesProjectIsNotFound() throws Exception {
        UUID id = UUID.randomUUID();
        when(projects.getOwned(id, OWNER)).thenThrow(new WebBlockException(ErrorCode.PROJECT_NOT_FOUND));
        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(OWNER)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
    }

    @Test
    @DisplayName("FR-16: PATCH /projects/{id} is gone, the name of a website cannot be changed")
    void fr16_renameNoLongerRoutes() throws Exception {
        mvc.perform(patch("/api/v1/projects/{id}", UUID.randomUUID())
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"New name\"}"))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(jsonPath("$.status").value(405));
        verifyNoInteractions(projects);
    }

    @Test
    void aTokenWithoutAUserIdIsForbidden() throws Exception {
        mvc.perform(get("/api/v1/projects").with(Users.withoutUserId()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("ACCESS_DENIED"));
        verifyNoInteractions(projects);
    }

    @Test
    @DisplayName("FR-14.2: the list and the detail carry businessTypeCode, businessTypeName and thumbnailUrl")
    void fr14_2_theDisplayFieldsAreInTheJson() throws Exception {
        UUID id = UUID.randomUUID();
        ProjectRecord project = record(id, "Shop");
        ProjectView view = ProjectView.of(project, "food_shop", "Foods", "/mock/templates/sweet-pastel.png", null);
        when(projects.listOwned(OWNER, 20, null, null, null)).thenReturn(new ProjectPage(List.of(project), null));
        when(projects.getOwned(id, OWNER)).thenReturn(project);
        when(views.views(List.of(project))).thenReturn(List.of(view));
        when(views.view(project)).thenReturn(view);
        mvc.perform(get("/api/v1/projects").with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].businessTypeCode").value("food_shop"))
                .andExpect(jsonPath("$.data[0].businessTypeName").value("Foods"))
                .andExpect(jsonPath("$.data[0].thumbnailUrl").value("/mock/templates/sweet-pastel.png"));
        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(OWNER)))
                .andExpect(jsonPath("$.data.businessTypeCode").value("food_shop"))
                .andExpect(jsonPath("$.data.businessTypeName").value("Foods"))
                .andExpect(jsonPath("$.data.thumbnailUrl").value("/mock/templates/sweet-pastel.png"));
    }

    @Test
    @DisplayName("FR-38.1: DELETE answers 204 with no body and passes the caller as the owner")
    void fr38_1_deleteAnswersNoContent() throws Exception {
        UUID id = UUID.randomUUID();
        mvc.perform(delete("/api/v1/projects/{id}", id).with(Users.user(OWNER)))
                .andExpect(status().isNoContent())
                .andExpect(content().string(""));
        verify(lifecycle).delete(id, OWNER);
    }

    @Test
    @DisplayName("FR-38.2: DELETE of a stranger's, an unknown or a deleted project is 404 PROJECT_NOT_FOUND")
    void fr38_2_deleteOfSomeoneElsesProjectIsNotFound() throws Exception {
        UUID id = UUID.randomUUID();
        doThrow(new WebBlockException(ErrorCode.PROJECT_NOT_FOUND))
                .when(lifecycle)
                .delete(id, OWNER);
        mvc.perform(delete("/api/v1/projects/{id}", id).with(Users.user(OWNER)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
    }

    @Test
    @DisplayName("FR-39.1: suspend and reactivate answer 200 with the website view")
    void fr39_1_suspendAndReactivateAnswerTheView() throws Exception {
        UUID id = UUID.randomUUID();
        when(lifecycle.suspend(id, OWNER)).thenReturn(record(id, "Shop", ProjectStatus.suspended));
        when(lifecycle.reactivate(id, OWNER)).thenReturn(record(id, "Shop", ProjectStatus.active));
        mvc.perform(post("/api/v1/projects/{id}/suspend", id).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("suspended"))
                .andExpect(jsonPath("$.data.readOnly").value(true));
        mvc.perform(post("/api/v1/projects/{id}/reactivate", id).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("active"))
                .andExpect(jsonPath("$.data.readOnly").value(false));
    }

    @Test
    @DisplayName("FR-39.5: suspend and reactivate of someone else's project are 404 PROJECT_NOT_FOUND")
    void fr39_5_suspendOfSomeoneElsesProjectIsNotFound() throws Exception {
        UUID id = UUID.randomUUID();
        when(lifecycle.suspend(id, OWNER)).thenThrow(new WebBlockException(ErrorCode.PROJECT_NOT_FOUND));
        when(lifecycle.reactivate(id, OWNER)).thenThrow(new WebBlockException(ErrorCode.PROJECT_NOT_FOUND));
        mvc.perform(post("/api/v1/projects/{id}/suspend", id).with(Users.user(OWNER)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        mvc.perform(post("/api/v1/projects/{id}/reactivate", id).with(Users.user(OWNER)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
    }

    @Test
    @DisplayName("FR-14.10: GET /projects/counts answers the totals of the caller and is not read as a project ID")
    void fr14_10_countsRouteIsNotAProjectId() throws Exception {
        when(projects.countOwned(OWNER)).thenReturn(new ProjectCounts(6, 3, 2, 1));
        mvc.perform(get("/api/v1/projects/counts").with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.all").value(6))
                .andExpect(jsonPath("$.data.draft").value(3))
                .andExpect(jsonPath("$.data.active").value(2))
                .andExpect(jsonPath("$.data.suspended").value(1));
        verify(projects, never()).getOwned(any(), any());
    }
}
