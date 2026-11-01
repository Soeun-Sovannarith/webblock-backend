package org.kshrd.webblock.project.presentation.rest.access;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.application.access.handler.AccessCheckService;
import org.kshrd.webblock.project.application.access.model.AccessAction;
import org.kshrd.webblock.project.application.access.model.AccessDecision;
import org.kshrd.webblock.project.application.access.model.AccessDenialReason;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

@WebMvcTest(controllers = AccessCheckController.class)
@Import(WebSliceSupport.Imports.class)
class AccessCheckControllerTest {

    static final UUID USER = UUID.randomUUID();
    static final UUID PROJECT = UUID.randomUUID();

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    AccessCheckService service;

    /** Provisioning's call: no Authorization header, the mesh policy is the gate (authentication design, A3). */
    private ResultActions call(String body) throws Exception {
        return mvc.perform(post("/internal/v1/access-checks")
                .contentType(MediaType.APPLICATION_JSON)
                .content(body));
    }

    private static String body(String action) {
        return "{\"userId\":\"%s\",\"projectId\":\"%s\",\"action\":\"%s\"}".formatted(USER, PROJECT, action);
    }

    @Test
    void anAllowedCheckHasNoReason() throws Exception {
        when(service.check(USER, PROJECT, AccessAction.LIST_RELEASES)).thenReturn(AccessDecision.ALLOWED);
        call(body("list_releases"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.allowed").value(true))
                .andExpect(jsonPath("$.data.reason").doesNotExist());
    }

    @Test
    void aDenialCarriesItsReasonWith200() throws Exception {
        when(service.check(USER, PROJECT, AccessAction.DEPLOY_RELEASE))
                .thenReturn(AccessDecision.denied(AccessDenialReason.NOT_FOUND));
        call(body("deploy_release"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.allowed").value(false))
                .andExpect(jsonPath("$.data.reason").value("NOT_FOUND"));
    }

    @Test
    void anUnknownActionIsABadRequestNeverAnAnswer() throws Exception {
        call(body("delete_everything")).andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }

    /**
     * The body an action outside the list answers (FR-30.6): 400 VALIDATION_FAILED with one field error, VALUE_INVALID
     * on action. The input value is never echoed.
     */
    @ParameterizedTest
    @ValueSource(strings = {"drop_db", "DEPLOY_RELEASE", "Deploy_Release", "deploy-release"})
    @DisplayName("FR-30.6, FR-32.3: an unknown (drop_db) or upper-case action is 400 VALIDATION_FAILED on action and"
            + " never reaches the service")
    void fr30_6_fr32_3_anUnknownOrUpperCaseActionIsVALIDATION_FAILED(String action) throws Exception {
        expectActionRefused(action);
    }

    /** FR-32: any value outside the list is bad input; REST reads the action exactly as sent, as gRPC does. */
    @ParameterizedTest
    @ValueSource(strings = {"deploy_release ", " get_release", " list_releases ", "\tdeploy_release", "get_release\n"})
    @DisplayName("FR-30.6, FR-32: an action with spaces around it is refused like any value outside the list")
    void fr30_6_fr32_anUntrimmedActionIsVALIDATION_FAILED(String action) throws Exception {
        expectActionRefused(action);
    }

    @Test
    @DisplayName("FR-30.6: a missing or null action is FIELD_REQUIRED on action")
    void fr30_6_aMissingActionIsFieldRequired() throws Exception {
        for (String body : new String[] {
            "{\"userId\":\"%s\",\"projectId\":\"%s\"}".formatted(USER, PROJECT),
            "{\"userId\":\"%s\",\"projectId\":\"%s\",\"action\":null}".formatted(USER, PROJECT)
        }) {
            call(body)
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors.length()").value(1))
                    .andExpect(jsonPath("$.errors[0].field").value("action"))
                    .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        }
        verifyNoInteractions(service);
    }

    private void expectActionRefused(String action) throws Exception {
        call(body(action.replace("\t", "\\t").replace("\n", "\\n")))
                .andExpect(status().isBadRequest())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON))
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors.length()").value(1))
                .andExpect(jsonPath("$.errors[0].field").value("action"))
                .andExpect(jsonPath("$.errors[0].code").value("VALUE_INVALID"))
                .andExpect(content().string(not(containsString(action.strip()))));
        verifyNoInteractions(service);
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    @DisplayName("FR-32: every listed action is read from its lower-case wire name")
    void fr32_everyActionIsReadFromItsWireName(AccessAction action) throws Exception {
        when(service.check(USER, PROJECT, action)).thenReturn(AccessDecision.ALLOWED);
        call(body(action.wireName()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.allowed").value(true));
    }

    @Test
    @DisplayName("FR-32.1: manage_site is accepted and the owner's answer is 200, allowed true, no reason")
    void fr32_1_manageSiteIsAllowedForTheOwner() throws Exception {
        when(service.check(USER, PROJECT, manageSite())).thenReturn(AccessDecision.ALLOWED);
        call(body("manage_site"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.allowed").value(true))
                .andExpect(jsonPath("$.data.reason").doesNotExist());
    }

    @Test
    @DisplayName("FR-32.2: manage_site on a deleted project is 200, allowed false, reason PROJECT_DELETED")
    void fr32_2_manageSiteOnADeletedProjectIsProjectDeleted() throws Exception {
        when(service.check(USER, PROJECT, manageSite()))
                .thenReturn(AccessDecision.denied(AccessDenialReason.PROJECT_DELETED));
        call(body("manage_site"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.allowed").value(false))
                .andExpect(jsonPath("$.data.reason").value("PROJECT_DELETED"));
    }

    /** Read through the wire name callers send. */
    private static AccessAction manageSite() {
        return AccessAction.fromWireName("manage_site").orElseThrow();
    }

    @Test
    void missingFieldsAreFieldErrors() throws Exception {
        call("{\"action\":\"get_release\"}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    @Test
    void numericActionsAreBadRequests() throws Exception {
        call(body("0").replace("\"0\"", "0")).andExpect(status().isBadRequest());
        call(body("0")).andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }

    @Test
    @DisplayName("FR-30.6: a short or non-canonical UUID is 400 VALIDATION_FAILED, UUID_INVALID on its field")
    void fr30_6_nonCanonicalIdsAreBadRequests() throws Exception {
        String base64 = "AZngogAAcACAAAAAAAAAqg==";
        String noDashes = USER.toString().replace("-", "");
        for (String id : new String[] {base64, noDashes, "1-1-1-1-1"}) {
            call(body("get_release").replace(USER.toString(), id))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors[0].field").value("userId"))
                    .andExpect(jsonPath("$.errors[0].code").value("UUID_INVALID"));
            call(body("get_release").replace(PROJECT.toString(), id))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors[0].field").value("projectId"))
                    .andExpect(jsonPath("$.errors[0].code").value("UUID_INVALID"));
        }
        verifyNoInteractions(service);
    }

    /** An old caller still sending a (stale) bearer is answered, and the token is never read. */
    @Test
    void aStaleBearerIsNeverRead() throws Exception {
        when(service.check(USER, PROJECT, AccessAction.GET_RELEASE)).thenReturn(AccessDecision.ALLOWED);
        mvc.perform(post("/internal/v1/access-checks")
                        .header("Authorization", "Bearer stale-or-garbage")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("get_release")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.allowed").value(true));
        verifyNoInteractions(jwtDecoder);
    }
}
