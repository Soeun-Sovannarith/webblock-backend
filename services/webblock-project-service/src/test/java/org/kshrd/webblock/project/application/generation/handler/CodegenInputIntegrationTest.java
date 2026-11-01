package org.kshrd.webblock.project.application.generation.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.generation.model.FailureCodes;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** The internal codegen-input endpoint on real PostgreSQL 18, the catalog stubbed. */
@ServiceIntegrationTest
class CodegenInputIntegrationTest {

    /** Unusual numbers, escapes and non-ASCII keys (plan, review focus 1). */
    static final String PUCK_TREE = """
            {"root":{"props":{"n":[1.0,1e2,-0,12345678901234567890,0.1000000000000000055511151231257827,0.0000001],"s":"\\u00e9","p":"a\\/b"}},
             "content":[{"type":"Hero","props":{"é":1,"e":2,"id":"h-1"}}],"zone":{}}""";

    @Autowired
    MockMvc mvc;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    GenerationTransitions transitions;

    @Autowired
    Clock clock;

    @Autowired
    JsonMapper json;

    @MockitoBean
    CatalogReference catalog;

    private GenerationRows rows() {
        return new GenerationRows(jdbc, clock);
    }

    /** A captured request on a one-product-type contract; the catalog answers that type and a business type. */
    private UUID captured(String input, UUID productTypeId, ProductTypeSchema schema) {
        UUID owner = UUID.randomUUID();
        UUID projectId = Uuids.v7();
        ProjectRecord project = tx.execute(s -> projects.create(
                new NewProject(projectId, owner, "Codegen", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        UUID contractId = rows().contract(projectId, owner);
        jdbc.update(
                "INSERT INTO project.generation_contract_schemas"
                        + " (generation_contract_id, product_type_schema_id, product_type_id) VALUES (?, ?, ?)",
                contractId,
                schema.productTypeSchemaId(),
                productTypeId);
        Map<UUID, ProductTypeSchema> bySchema = new LinkedHashMap<>();
        bySchema.put(schema.productTypeSchemaId(), schema);
        when(catalog.productTypeSchemas(anyCollection())).thenReturn(bySchema);
        when(catalog.businessType(project.businessTypeId()))
                .thenReturn(Optional.of(
                        new BusinessType(project.businessTypeId(), "clothing_shop", "Clothing shop", true)));
        return rows().captured(projectId, contractId, input);
    }

    private UUID captured(String input) {
        UUID typeId = Uuids.v7();
        return captured(input, typeId, schema(typeId));
    }

    private static ProductTypeSchema schema(UUID typeId) {
        JsonNode fieldSchema = JsonMapper.builder().build().createObjectNode().put("kind", "object");
        return new ProductTypeSchema(typeId, "top", Uuids.v7(), 1, fieldSchema);
    }

    private void moveTo(UUID requestId, GenerationRequestStatus to) {
        tx.executeWithoutResult(s -> transitions.move(
                requestId,
                GenerationRequestStatus.captured,
                to,
                to == GenerationRequestStatus.failed ? FailureCodes.ADMISSION_ERROR : null));
    }

    private UUID projectOf(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT project_id FROM project.generation_requests WHERE generation_request_id = ?",
                UUID.class,
                requestId);
    }

    private String url(UUID requestId) {
        return "/internal/v1/generation-requests/" + requestId + "/codegen-input";
    }

    @Test
    void aGeneratingRequestAnswersItsInputWithoutAToken() throws Exception {
        UUID typeId = Uuids.v7();
        ProductTypeSchema schema = schema(typeId);
        UUID requestId = captured("{\"pages\":[]}", typeId, schema);
        moveTo(requestId, GenerationRequestStatus.generating);

        mvc.perform(get(url(requestId)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.generationRequestId").value(requestId.toString()))
                .andExpect(
                        jsonPath("$.data.projectId").value(projectOf(requestId).toString()))
                .andExpect(jsonPath("$.data.businessTypeCode").value("clothing_shop"))
                .andExpect(jsonPath("$.data.capturedInput.pages").isArray())
                .andExpect(jsonPath("$.data.capturedInputHash").value(InputHasher.sha256("{\"pages\":[]}")))
                .andExpect(
                        jsonPath("$.data.generationContract.apiContractVersion").value("1.0.0"))
                .andExpect(jsonPath("$.data.generationContract.generationContractId")
                        .isString())
                .andExpect(jsonPath("$.data.generationContract.productTypes[0].productTypeId")
                        .value(typeId.toString()))
                .andExpect(jsonPath("$.data.generationContract.productTypes[0].code")
                        .value("top"))
                .andExpect(jsonPath("$.data.generationContract.productTypes[0].productTypeSchemaId")
                        .value(schema.productTypeSchemaId().toString()))
                .andExpect(jsonPath("$.data.generationContract.productTypes[0].fieldSchema.kind")
                        .value("object"));
    }

    /** The exact key set: no scaffold and no limits (J4). */
    @Test
    void theBodyHasExactlyTheDocumentedKeys() throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        moveTo(requestId, GenerationRequestStatus.generating);

        String body = mvc.perform(get(url(requestId)))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString();
        JsonNode data = json.readTree(body).get("data");
        assertThat(data.propertyNames())
                .containsExactlyInAnyOrder(
                        "generationRequestId",
                        "projectId",
                        "businessTypeCode",
                        "capturedInput",
                        "capturedInputHash",
                        "generationContract");
        assertThat(data.get("generationContract").propertyNames())
                .containsExactlyInAnyOrder("generationContractId", "apiContractVersion", "productTypes");
        assertThat(data.get("generationContract").get("productTypes").get(0).propertyNames())
                .containsExactlyInAnyOrder("productTypeId", "code", "productTypeSchemaId", "fieldSchema");
    }

    @Test
    void storedJsonbReCanonicalizesToTheStoredHash() throws Exception {
        UUID requestId = captured(PUCK_TREE);
        moveTo(requestId, GenerationRequestStatus.generating);

        String storedText = jdbc.queryForObject(
                "SELECT captured_input::text FROM project.generation_requests WHERE generation_request_id = ?",
                String.class,
                requestId);
        String storedHash = jdbc.queryForObject(
                "SELECT captured_input_hash FROM project.generation_requests WHERE generation_request_id = ?",
                String.class,
                requestId);
        assertThat(InputHasher.sha256(storedText)).isEqualTo(storedHash);

        String body = mvc.perform(get(url(requestId)))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString();
        // Byte for byte as stored: no number changes form (0.0000001, 17+ digits, 1e2 all stay as jsonb prints them).
        assertThat(body).contains("\"capturedInput\":" + storedText + ",");
        assertThat(storedText).contains("0.1000000000000000055511151231257827", "0.0000001");
        JsonNode data = json.readTree(body).get("data");
        assertThat(data.get("capturedInputHash").asString()).isEqualTo(storedHash);
        assertThat(InputHasher.sha256(data.get("capturedInput").toString())).isEqualTo(storedHash);
        // What Codegen parses from the raw response text must give the same hash.
        assertThat(InputHasher.sha256(
                        json.writeValueAsString(json.readTree(body).get("data").get("capturedInput"))))
                .isEqualTo(storedHash);
    }

    @Test
    void aGarbageBearerIsIgnored() throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        moveTo(requestId, GenerationRequestStatus.generating);

        mvc.perform(get(url(requestId)).header("Authorization", "Bearer x")).andExpect(status().isOk());
    }

    @Test
    @org.junit.jupiter.api.DisplayName("FR-38.7: a generating request of a deleted project is not served: 404")
    void fr38_7_aDeletedProjectsRequestIsNotFound() throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        moveTo(requestId, GenerationRequestStatus.generating);
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", projectOf(requestId));

        mvc.perform(get(url(requestId)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("GENERATION_REQUEST_NOT_FOUND"));
    }

    @Test
    @org.junit.jupiter.api.DisplayName("FR-39.6: a suspended project's generating request may still finish: 200")
    void fr39_6_aSuspendedProjectsGeneratingRequestIsServed() throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        moveTo(requestId, GenerationRequestStatus.generating);
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", projectOf(requestId));

        mvc.perform(get(url(requestId))).andExpect(status().isOk());
    }

    @Test
    void anUnknownRequestIs404() throws Exception {
        mvc.perform(get(url(UUID.randomUUID())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("GENERATION_REQUEST_NOT_FOUND"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"captured", "failed"})
    void aRequestNotGeneratingIs409(String status) throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        if (status.equals("failed")) {
            moveTo(requestId, GenerationRequestStatus.failed);
        }

        mvc.perform(get(url(requestId)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("REQUEST_NOT_GENERATING"));
    }

    @Test
    void aFrozenSchemaMissingFromTheCatalogIs500WithNoDetails() throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        moveTo(requestId, GenerationRequestStatus.generating);
        when(catalog.productTypeSchemas(anyCollection())).thenReturn(Map.of());

        mvc.perform(get(url(requestId)))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"))
                .andExpect(jsonPath("$.errors").isEmpty());
    }

    @Test
    void aMissingBusinessTypeIs500WithNoDetails() throws Exception {
        UUID requestId = captured("{\"pages\":[]}");
        moveTo(requestId, GenerationRequestStatus.generating);
        when(catalog.businessType(any(UUID.class))).thenReturn(Optional.empty());

        mvc.perform(get(url(requestId)))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"))
                .andExpect(jsonPath("$.errors").isEmpty());
    }

    @Test
    void productTypesAreInAscendingIdOrder() throws Exception {
        UUID high = UUID.fromString("f0000000-0000-4000-8000-000000000001");
        UUID low = UUID.fromString("10000000-0000-4000-8000-000000000001");
        ProductTypeSchema highSchema = schema(high);
        ProductTypeSchema lowSchema = schema(low);
        UUID requestId = captured("{\"pages\":[]}", high, highSchema);
        UUID contractId = jdbc.queryForObject(
                "SELECT generation_contract_id FROM project.generation_requests WHERE generation_request_id = ?",
                UUID.class,
                requestId);
        jdbc.update(
                "INSERT INTO project.generation_contract_schemas"
                        + " (generation_contract_id, product_type_schema_id, product_type_id) VALUES (?, ?, ?)",
                contractId,
                lowSchema.productTypeSchemaId(),
                low);
        Map<UUID, ProductTypeSchema> both = new LinkedHashMap<>();
        both.put(highSchema.productTypeSchemaId(), highSchema);
        both.put(lowSchema.productTypeSchemaId(), lowSchema);
        when(catalog.productTypeSchemas(anyCollection())).thenReturn(both);
        moveTo(requestId, GenerationRequestStatus.generating);

        String body = mvc.perform(get(url(requestId)))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString();
        JsonNode types =
                json.readTree(body).get("data").get("generationContract").get("productTypes");
        assertThat(types.get(0).get("productTypeId").asString()).isEqualTo(low.toString());
        assertThat(types.get(1).get("productTypeId").asString()).isEqualTo(high.toString());
        assertThat(types.size()).isEqualTo(2);
    }
}
