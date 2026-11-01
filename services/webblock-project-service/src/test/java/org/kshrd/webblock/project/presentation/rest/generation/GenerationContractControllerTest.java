package org.kshrd.webblock.project.presentation.rest.generation;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
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
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.handler.GenerationContractService;
import org.kshrd.webblock.project.application.generation.model.ContractProductType;
import org.kshrd.webblock.project.application.generation.model.GenerationContractPage;
import org.kshrd.webblock.project.application.generation.model.GenerationContractRecord;
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
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = GenerationContractController.class)
@Import(WebSliceSupport.Imports.class)
class GenerationContractControllerTest {

    static final UUID OWNER = UUID.fromString("0199e0a2-0000-7000-8000-0000000000aa");
    static final UUID PROJECT = UUID.fromString("0199e0a2-0000-7000-8000-0000000000bb");
    static final UUID TYPE = UUID.fromString("0199e0a2-0000-7000-8000-0000000000cc");
    static final String PATH = "/api/v1/projects/{projectId}/generation-contracts";
    static final List<String> CONTRACT_KEYS = List.of(
            "generationContractId",
            "versionNumber",
            "apiContractVersion",
            "productTypes",
            "createdByUserId",
            "createdAt");

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    GenerationContractService contracts;

    private static GenerationContractRecord contract(UUID generationContractId, int versionNumber) {
        return new GenerationContractRecord(
                generationContractId,
                versionNumber,
                "1.0.0",
                List.of(new ContractProductType(TYPE, 2)),
                OWNER,
                Instant.parse("2026-10-18T00:00:00Z"));
    }

    private ResultActions postBody(String body) throws Exception {
        return mvc.perform(post(PATH, PROJECT)
                .with(Users.user(OWNER))
                .contentType(MediaType.APPLICATION_JSON)
                .content(body));
    }

    /** Lead ruling C11: exactly these members, and the product type's two. */
    private static void assertContractKeys(JsonNode contract) {
        assertThat(contract.propertyNames()).containsExactlyInAnyOrderElementsOf(CONTRACT_KEYS);
        assertThat(contract.get("productTypes").get(0).propertyNames())
                .containsExactlyInAnyOrder("productTypeId", "schemaVersion");
    }

    private static JsonNode body(ResultActions result) throws Exception {
        return JsonMapper.builder()
                .build()
                .readTree(result.andReturn().getResponse().getContentAsString());
    }

    @Test
    void createAnswers201WithTheContractBodyAndNoLocation() throws Exception {
        UUID contractId = UUID.randomUUID();
        when(contracts.create(PROJECT, OWNER, List.of(TYPE))).thenReturn(contract(contractId, 1));
        ResultActions result = postBody("{\"productTypeIds\":[\"" + TYPE + "\"]}")
                .andExpect(status().isCreated())
                .andExpect(header().doesNotExist("Location"))
                .andExpect(jsonPath("$.data.generationContractId").value(contractId.toString()))
                .andExpect(jsonPath("$.data.versionNumber").value(1))
                .andExpect(jsonPath("$.data.apiContractVersion").value("1.0.0"))
                .andExpect(jsonPath("$.data.productTypes[0].productTypeId").value(TYPE.toString()))
                .andExpect(jsonPath("$.data.productTypes[0].schemaVersion").value(2))
                .andExpect(jsonPath("$.data.createdByUserId").value(OWNER.toString()))
                .andExpect(jsonPath("$.data.createdAt").value("2026-10-18T00:00:00Z"));
        assertContractKeys(body(result).get("data"));
    }

    @Test
    void anEmptyListIsAValidationError() throws Exception {
        postBody("{\"productTypeIds\":[]}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors[0].field").value("productTypeIds"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        postBody("{}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("productTypeIds"));
        verifyNoInteractions(contracts);
    }

    @Test
    void duplicatesAreAValidationError() throws Exception {
        postBody("{\"productTypeIds\":[\"" + TYPE + "\",\"" + TYPE + "\"]}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors[0].field").value("productTypeIds"))
                .andExpect(jsonPath("$.errors[0].code").value("VALUE_INVALID"));
        verifyNoInteractions(contracts);
    }

    @Test
    void aNullEntryOrANonUuidIsAValidationError() throws Exception {
        postBody("{\"productTypeIds\":[null]}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
        postBody("{\"productTypeIds\":[\"x\"]}").andExpect(status().isBadRequest());
        verifyNoInteractions(contracts);
    }

    /** J2-3: Project sets apiContractVersion; the strict mapper refuses it as an unknown member. */
    @Test
    void anApiContractVersionInTheBodyIsRefused() throws Exception {
        postBody("{\"productTypeIds\":[\"" + TYPE + "\"],\"apiContractVersion\":\"9.9.9\"}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
        verifyNoInteractions(contracts);
    }

    @Test
    void listIsEnvelopedWithItsPage() throws Exception {
        UUID contractId = UUID.randomUUID();
        when(contracts.list(PROJECT, OWNER, 20, null))
                .thenReturn(new GenerationContractPage(List.of(contract(contractId, 3)), "next"));
        ResultActions result = mvc.perform(get(PATH, PROJECT).with(Users.user(OWNER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].generationContractId").value(contractId.toString()))
                .andExpect(jsonPath("$.data[0].versionNumber").value(3))
                .andExpect(jsonPath("$.meta.page.limit").value(20))
                .andExpect(jsonPath("$.meta.page.nextCursor").value("next"));
        assertContractKeys(body(result).get("data").get(0));
    }

    @Test
    void theListPassesItsCursorAndRefusesALimitOutOfRange() throws Exception {
        when(contracts.list(eq(PROJECT), eq(OWNER), eq(5), eq("abc")))
                .thenReturn(new GenerationContractPage(List.of(), null));
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
    void withoutATokenIs401() throws Exception {
        mvc.perform(get(PATH, PROJECT)).andExpect(status().isUnauthorized());
        mvc.perform(post(PATH, PROJECT)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"productTypeIds\":[\"" + TYPE + "\"]}"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(contracts);
    }
}
