package org.kshrd.webblock.project.presentation.rest.generation;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.application.generation.model.CaptureOutcome;
import org.kshrd.webblock.project.application.generation.model.GenerationRequestPage;
import org.kshrd.webblock.project.application.generation.model.GenerationRequestRecord;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = GenerationRequestController.class)
@Import(WebSliceSupport.Imports.class)
class GenerationRequestControllerTest {

    static final UUID OWNER = UUID.fromString("0199e0a2-0000-7000-8000-0000000000aa");
    static final UUID PROJECT = UUID.fromString("0199e0a2-0000-7000-8000-0000000000bb");
    static final UUID CONTRACT = UUID.fromString("0199e0a2-0000-7000-8000-0000000000cc");
    static final UUID REQUEST = UUID.fromString("0199e0a2-0000-7000-8000-0000000000dd");
    static final String PATH = "/api/v1/projects/{projectId}/generation-requests";
    static final String KEY = "retry-key-1";
    static final List<String> RECORD_KEYS =
            List.of("generationRequestId", "status", "failureCode", "createdAt", "updatedAt");

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    GenerationRequestService requests;

    private static MockHttpServletRequestBuilder capture(String body) {
        return post(PATH, PROJECT)
                .with(Users.user(OWNER))
                .contentType(MediaType.APPLICATION_JSON)
                .content(body);
    }

    private static String contractBody() {
        return "{\"generationContractId\":\"" + CONTRACT + "\"}";
    }

    private static GenerationRequestRecord record(UUID generationRequestId) {
        return new GenerationRequestRecord(
                generationRequestId,
                GenerationRequestStatus.failed,
                "no_entitlement",
                Instant.parse("2026-10-18T00:00:00Z"),
                Instant.parse("2026-10-18T00:00:01Z"));
    }

    private static JsonNode data(ResultActions result) throws Exception {
        return JsonMapper.builder()
                .build()
                .readTree(result.andReturn().getResponse().getContentAsString())
                .get("data");
    }

    @Test
    void aMissingOrInvalidIdempotencyKeyIs400() throws Exception {
        mvc.perform(capture(contractBody()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors[0].field").value("Idempotency-Key"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        for (String key : List.of("", "k".repeat(256))) {
            mvc.perform(capture(contractBody()).header("Idempotency-Key", key))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors[0].field").value("Idempotency-Key"))
                    .andExpect(jsonPath("$.errors[0].code").value("IDEMPOTENCY_KEY_INVALID"));
        }
        verifyNoInteractions(requests);
    }

    @Test
    @DisplayName("FR-24.7: a body without a contract ID, with null, or no body reaches the service with no contract")
    void fr24_7_aMissingContractIdIsPassedOnAsNull() throws Exception {
        when(requests.capture(PROJECT, OWNER, KEY, null))
                .thenReturn(new CaptureOutcome(REQUEST, GenerationRequestStatus.captured, true));
        mvc.perform(capture("{}").header("Idempotency-Key", KEY)).andExpect(status().isAccepted());
        mvc.perform(capture("{\"generationContractId\":null}").header("Idempotency-Key", KEY))
                .andExpect(status().isAccepted());
        mvc.perform(post(PATH, PROJECT).with(Users.user(OWNER)).header("Idempotency-Key", KEY))
                .andExpect(status().isAccepted());
        verify(requests, times(3)).capture(PROJECT, OWNER, KEY, null);
    }

    @Test
    @DisplayName("FR-24.7: a contract ID that is not a UUID is still 400")
    void fr24_7_aMalformedContractIdIs400() throws Exception {
        mvc.perform(capture("{\"generationContractId\":\"x\"}").header("Idempotency-Key", KEY))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(requests);
    }

    @Test
    void aCreatedCaptureIs202WithLocation() throws Exception {
        when(requests.capture(PROJECT, OWNER, KEY, CONTRACT))
                .thenReturn(new CaptureOutcome(REQUEST, GenerationRequestStatus.captured, true));
        ResultActions result = mvc.perform(capture(contractBody()).header("Idempotency-Key", KEY))
                .andExpect(status().isAccepted())
                .andExpect(
                        header().string("Location", "/api/v1/projects/" + PROJECT + "/generation-requests/" + REQUEST))
                .andExpect(jsonPath("$.data.generationRequestId").value(REQUEST.toString()))
                .andExpect(jsonPath("$.data.status").value("captured"));
        assertThat(data(result).propertyNames()).containsExactlyInAnyOrder("generationRequestId", "status");
    }

    /** Decision P7 / J2-26: the same shape, the status as the row has it now; no Location. */
    @Test
    void aReplayIs200WithTheSameBody() throws Exception {
        when(requests.capture(PROJECT, OWNER, KEY, CONTRACT))
                .thenReturn(new CaptureOutcome(REQUEST, GenerationRequestStatus.generating, false));
        ResultActions result = mvc.perform(capture(contractBody()).header("Idempotency-Key", KEY))
                .andExpect(status().isOk())
                .andExpect(header().doesNotExist("Location"))
                .andExpect(jsonPath("$.data.generationRequestId").value(REQUEST.toString()))
                .andExpect(jsonPath("$.data.status").value("generating"));
        assertThat(data(result).propertyNames()).containsExactlyInAnyOrder("generationRequestId", "status");
    }

    @Test
    void theListIsEnvelopedWithItsPageAndItsItemsCarryExactlyTheRecordFields() throws Exception {
        when(requests.list(PROJECT, OWNER, 20, null))
                .thenReturn(new GenerationRequestPage(List.of(record(REQUEST)), "next"));
        ResultActions result = mvc.perform(get(PATH, PROJECT).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].generationRequestId").value(REQUEST.toString()))
                .andExpect(jsonPath("$.data[0].status").value("failed"))
                .andExpect(jsonPath("$.data[0].failureCode").value("no_entitlement"))
                .andExpect(jsonPath("$.data[0].createdAt").value("2026-10-18T00:00:00Z"))
                .andExpect(jsonPath("$.data[0].updatedAt").value("2026-10-18T00:00:01Z"))
                .andExpect(jsonPath("$.meta.page.limit").value(20))
                .andExpect(jsonPath("$.meta.page.nextCursor").value("next"));
        assertThat(data(result).get(0).propertyNames()).containsExactlyInAnyOrderElementsOf(RECORD_KEYS);
    }

    @Test
    void theListPassesItsCursorAndRefusesALimitOutOfRange() throws Exception {
        when(requests.list(PROJECT, OWNER, 5, "abc")).thenReturn(new GenerationRequestPage(List.of(), null));
        mvc.perform(get(PATH, PROJECT)
                        .param("limit", "5")
                        .param("cursor", "abc")
                        .with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.meta.page.limit").value(5));
        mvc.perform(get(PATH, PROJECT).param("limit", "101").with(Users.user(OWNER)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        mvc.perform(get(PATH, PROJECT).param("limit", "0").with(Users.user(OWNER)))
                .andExpect(status().isBadRequest());
    }

    @Test
    void getOneCarriesExactlyTheListItemFields() throws Exception {
        when(requests.get(PROJECT, OWNER, REQUEST)).thenReturn(record(REQUEST));
        ResultActions result = mvc.perform(
                        get(PATH + "/{generationRequestId}", PROJECT, REQUEST).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.generationRequestId").value(REQUEST.toString()))
                .andExpect(jsonPath("$.data.status").value("failed"));
        assertThat(data(result).propertyNames()).containsExactlyInAnyOrderElementsOf(RECORD_KEYS);
    }

    @Test
    void withoutATokenIs401() throws Exception {
        mvc.perform(get(PATH, PROJECT)).andExpect(status().isUnauthorized());
        mvc.perform(get(PATH + "/{generationRequestId}", PROJECT, REQUEST)).andExpect(status().isUnauthorized());
        mvc.perform(post(PATH, PROJECT)
                        .header("Idempotency-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(contractBody()))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(requests);
    }
}
