package org.kshrd.webblock.project.application.generation.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.time.Clock;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Deploy in one call: a capture without a contract ID uses or makes the contract of all the active product types of
 * the business type (FR-24.7 to FR-24.10, FR-22.5), on real PostgreSQL 18 and RabbitMQ. The catalog is a mock that
 * answers two active product types.
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=8",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class OneCallDeployIntegrationTest {

    static final String PATH = "/api/v1/projects/{projectId}/generation-requests";

    @MockitoBean
    PaymentAdmissions payment;

    @MockitoBean
    CatalogReference catalog;

    @Autowired
    MockMvc mvc;

    @Autowired
    ProjectService projects;

    @Autowired
    GenerationTransitions transitions;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JdbcClient db;

    @Autowired
    JsonMapper json;

    @Autowired
    Clock clock;

    final List<UUID> testProjects = new CopyOnWriteArrayList<>();

    record Owned(UUID owner, UUID projectId) {}

    record Answer(int status, JsonNode body) {}

    private Map<UUID, ProductTypeSchema> active;

    @BeforeEach
    void setUp() {
        doAnswer(call -> GenerationAdmission.refused("NO_ENTITLEMENT"))
                .when(payment)
                .admitGeneration(any(), any(), any());
        active = new LinkedHashMap<>();
        for (String code : List.of("product", "article")) {
            UUID typeId = Uuids.v7();
            active.put(
                    typeId,
                    new ProductTypeSchema(
                            typeId,
                            code,
                            Uuids.v7(),
                            1,
                            JsonMapper.builder().build().createObjectNode().put("kind", "object")));
        }
        when(catalog.allActiveProductTypeSchemas(any())).thenReturn(active);
        when(catalog.activeProductTypeSchemas(any(), anyCollection())).thenReturn(active);
    }

    @AfterEach
    void finishJobs() {
        await().atMost(Duration.ofSeconds(60))
                .until(() -> db.sql("SELECT count(*) FROM " + OutboxRecords.TABLE
                                        + " o JOIN project.generation_requests r"
                                        + " ON o.record_key = 'generation:' || r.generation_request_id"
                                        + " WHERE r.project_id = ANY(?) AND o.status <> ?")
                                .params(testProjects.toArray(UUID[]::new), OutboxRecords.COMPLETED)
                                .query(Long.class)
                                .single()
                        == 0);
    }

    private Owned project() {
        UUID owner = Uuids.v7();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(new NewProject(
                projectId, owner, "One call", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        testProjects.add(projectId);
        return new Owned(owner, projectId);
    }

    private Answer capture(UUID caller, UUID projectId, String key, String body) throws Exception {
        MvcResult result = mvc.perform(post(PATH, projectId)
                        .with(Users.user(caller))
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andReturn();
        return new Answer(
                result.getResponse().getStatus(),
                json.readTree(result.getResponse().getContentAsString()));
    }

    private Answer captureWithoutContract(Owned p, String key) throws Exception {
        return capture(p.owner(), p.projectId(), key, "{}");
    }

    private static UUID requestId(Answer answer) {
        return UUID.fromString(
                answer.body().get("data").get("generationRequestId").asString());
    }

    private long contractRows(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT count(*) FROM project.generation_contracts WHERE project_id = ?", Long.class, projectId);
    }

    private long requestRows(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT count(*) FROM project.generation_requests WHERE project_id = ?", Long.class, projectId);
    }

    private UUID contractOf(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT generation_contract_id FROM project.generation_requests WHERE generation_request_id = ?",
                UUID.class,
                requestId);
    }

    /** Waits for the refusing admission job to fail the request, which frees the project's in-flight slot. */
    private void awaitEnded(UUID requestId) {
        await().atMost(Duration.ofSeconds(30))
                .until(() -> "failed"
                        .equals(jdbc.queryForObject(
                                "SELECT status::text FROM project.generation_requests WHERE generation_request_id = ?",
                                String.class,
                                requestId)));
    }

    @Test
    @DisplayName("FR-24.7: a capture without a contract ID makes a contract of every active type and captures (202)")
    void fr24_7_aCaptureWithoutAContractMakesOneAndCaptures() throws Exception {
        Owned p = project();

        Answer answer = captureWithoutContract(p, "one-call");

        assertThat(answer.status()).isEqualTo(202);
        assertThat(answer.body().get("data").get("status").asString()).isEqualTo("captured");
        assertThat(contractRows(p.projectId())).isEqualTo(1);
        UUID contractId = contractOf(requestId(answer));
        List<UUID> frozenTypes = jdbc.queryForList(
                "SELECT product_type_id FROM project.generation_contract_schemas WHERE generation_contract_id = ?",
                UUID.class,
                contractId);
        assertThat(frozenTypes).containsExactlyInAnyOrderElementsOf(active.keySet());
        awaitEnded(requestId(answer));
    }

    @Test
    @DisplayName("FR-24.7: no body at all, and a null contract ID, work as well")
    void fr24_7_noBodyAndNullAreTheSame() throws Exception {
        Owned p = project();
        MvcResult noBody = mvc.perform(
                        post(PATH, p.projectId()).with(Users.user(p.owner())).header("Idempotency-Key", "no-body"))
                .andReturn();
        assertThat(noBody.getResponse().getStatus()).isEqualTo(202);
        UUID first = UUID.fromString(json.readTree(noBody.getResponse().getContentAsString())
                .get("data")
                .get("generationRequestId")
                .asString());
        awaitEnded(first);

        Answer withNull = capture(p.owner(), p.projectId(), "null-id", "{\"generationContractId\":null}");

        assertThat(withNull.status()).isEqualTo(202);
        assertThat(contractRows(p.projectId())).isEqualTo(1);
        awaitEnded(requestId(withNull));
    }

    @Test
    @DisplayName("FR-24.8: a later capture without a contract ID reuses the same contract")
    void fr24_8_aLaterCaptureReusesTheContract() throws Exception {
        Owned p = project();
        Answer first = captureWithoutContract(p, "first");
        assertThat(first.status()).isEqualTo(202);
        awaitEnded(requestId(first));

        Answer second = captureWithoutContract(p, "second");

        assertThat(second.status()).isEqualTo(202);
        assertThat(requestId(second)).isNotEqualTo(requestId(first));
        assertThat(contractOf(requestId(second))).isEqualTo(contractOf(requestId(first)));
        assertThat(contractRows(p.projectId())).isEqualTo(1);
        awaitEnded(requestId(second));
    }

    @Test
    @DisplayName("FR-24.8: when the catalog schema changed, the next capture makes a new contract")
    void fr24_8_aChangedCatalogSchemaMakesANewContract() throws Exception {
        Owned p = project();
        Answer first = captureWithoutContract(p, "before");
        awaitEnded(requestId(first));
        UUID typeId = active.keySet().iterator().next();
        ProductTypeSchema old = active.get(typeId);
        active.put(typeId, new ProductTypeSchema(typeId, old.code(), Uuids.v7(), 2, old.fieldSchema()));

        Answer second = captureWithoutContract(p, "after");

        assertThat(second.status()).isEqualTo(202);
        assertThat(contractOf(requestId(second))).isNotEqualTo(contractOf(requestId(first)));
        assertThat(contractRows(p.projectId())).isEqualTo(2);
        awaitEnded(requestId(second));
    }

    @Test
    @DisplayName("FR-24.1: an explicit contract ID still works and makes no contract")
    void fr24_1_anExplicitContractStillWorks() throws Exception {
        Owned p = project();
        UUID contractId = Uuids.v7();
        jdbc.update(
                "INSERT INTO project.generation_contracts (generation_contract_id, project_id, version_number,"
                        + " api_contract_version, created_by_user_id, created_at) VALUES (?, ?, 1, '1.0.0', ?, now())",
                contractId,
                p.projectId(),
                p.owner());

        Answer answer =
                capture(p.owner(), p.projectId(), "explicit", "{\"generationContractId\":\"" + contractId + "\"}");

        assertThat(answer.status()).isEqualTo(202);
        assertThat(contractOf(requestId(answer))).isEqualTo(contractId);
        assertThat(contractRows(p.projectId())).isEqualTo(1);
        awaitEnded(requestId(answer));
    }

    @Test
    @DisplayName("FR-24.9: a replay without a contract ID answers the same request and makes no second contract")
    void fr24_9_aReplayWithoutAContractMakesNothing() throws Exception {
        Owned p = project();
        Answer created = captureWithoutContract(p, "replay");
        assertThat(created.status()).isEqualTo(202);
        awaitEnded(requestId(created));

        Answer replay = captureWithoutContract(p, "replay");
        Answer reusedWithContract = capture(
                p.owner(),
                p.projectId(),
                "replay",
                "{\"generationContractId\":\"" + contractOf(requestId(created)) + "\"}");

        assertThat(replay.status()).isEqualTo(200);
        assertThat(requestId(replay)).isEqualTo(requestId(created));
        assertThat(reusedWithContract.status()).isEqualTo(409);
        assertThat(reusedWithContract.body().get("code").asString()).isEqualTo("IDEMPOTENCY_KEY_REUSED");
        assertThat(contractRows(p.projectId())).isEqualTo(1);
        assertThat(requestRows(p.projectId())).isEqualTo(1);
    }

    @Test
    @DisplayName("FR-24.10: a stranger gets 404 PROJECT_NOT_FOUND and no contract is made")
    void fr24_10_aStrangerMakesNoContract() throws Exception {
        Owned p = project();

        Answer answer = capture(Uuids.v7(), p.projectId(), "stranger", "{}");

        assertThat(answer.status()).isEqualTo(404);
        assertThat(answer.body().get("code").asString()).isEqualTo("PROJECT_NOT_FOUND");
        assertThat(contractRows(p.projectId())).isZero();
        assertThat(requestRows(p.projectId())).isZero();
    }

    @Test
    @DisplayName("FR-24.10: a read-only website is 422 PROJECT_READ_ONLY and no contract is made")
    void fr24_10_aSuspendedWebsiteMakesNoContract() throws Exception {
        Owned p = project();
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", p.projectId());

        Answer answer = captureWithoutContract(p, "suspended");

        assertThat(answer.status()).isEqualTo(422);
        assertThat(answer.body().get("code").asString()).isEqualTo("PROJECT_READ_ONLY");
        assertThat(contractRows(p.projectId())).isZero();
        assertThat(requestRows(p.projectId())).isZero();
    }

    @Test
    @DisplayName(
            "FR-24.10: the catalog down when a contract must be made is 503 CATALOG_UNAVAILABLE; nothing is stored")
    void fr24_10_catalogDownStoresNothing() throws Exception {
        Owned p = project();
        when(catalog.allActiveProductTypeSchemas(any()))
                .thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));

        Answer answer = captureWithoutContract(p, "catalog-down");

        assertThat(answer.status()).isEqualTo(503);
        assertThat(answer.body().get("code").asString()).isEqualTo("CATALOG_UNAVAILABLE");
        assertThat(contractRows(p.projectId())).isZero();
        assertThat(requestRows(p.projectId())).isZero();
    }

    @Test
    @DisplayName("FR-24.4: one request in flight still refuses a second capture without a contract ID")
    void fr24_4_aRequestInFlightStillRefuses() throws Exception {
        Owned p = project();
        UUID contractId = Uuids.v7();
        jdbc.update(
                "INSERT INTO project.generation_contracts (generation_contract_id, project_id, version_number,"
                        + " api_contract_version, created_by_user_id, created_at) VALUES (?, ?, 1, '1.0.0', ?, now())",
                contractId,
                p.projectId(),
                p.owner());
        // A request already in flight: written directly, so no admission job is involved.
        new GenerationRows(jdbc, clock).captured(p.projectId(), contractId, "{}");

        Answer answer = captureWithoutContract(p, "second");

        assertThat(answer.status()).isEqualTo(409);
        assertThat(answer.body().get("code").asString()).isEqualTo("GENERATION_IN_PROGRESS");
        assertThat(requestRows(p.projectId())).isEqualTo(1);
    }
}
