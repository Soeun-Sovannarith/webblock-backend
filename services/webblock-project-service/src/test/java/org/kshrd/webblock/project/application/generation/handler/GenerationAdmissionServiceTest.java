package org.kshrd.webblock.project.application.generation.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.time.Clock;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationRequestRepository;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.MutableClock;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Limit;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.convention.TestBean;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * The admission job's work, called directly: Payment is a mock and the clock a MutableClock (the application's
 * {@code clock} bean, replaced). Its own application context, kept small and closed after the class (see
 * ProjectCreationRefusalIntegrationTest).
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=4",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2",
            "webblock.generation.admission-window=15m"
        })
class GenerationAdmissionServiceTest {

    static final MutableClock CLOCK = new MutableClock();
    static final String INPUT = "{\"componentCatalogVersion\":\"0.1.0\",\"pages\":[]}";

    @TestBean(name = "clock", methodName = "mutableClock")
    Clock clock;

    static Clock mutableClock() {
        return CLOCK;
    }

    @MockitoBean
    PaymentAdmissions payment;

    @Autowired
    GenerationAdmissionService admissions;

    @Autowired
    GenerationTransitions transitions;

    @Autowired
    GenerationRequestRepository requests;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JdbcClient db;

    @Autowired
    OutboxRecords outbox;

    @Autowired
    JsonMapper json;

    GenerationRows rows;

    record Captured(UUID owner, UUID projectId, UUID requestId) {}

    record Row(String status, String failureCode, OffsetDateTime updatedAt) {}

    @BeforeEach
    void rows() {
        rows = new GenerationRows(jdbc, CLOCK);
    }

    private Captured captured() {
        UUID owner = UUID.randomUUID();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Gen", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        UUID contractId = rows.contract(projectId, owner);
        return new Captured(owner, projectId, rows.captured(projectId, contractId, INPUT));
    }

    private Row row(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT status::text, failure_code, updated_at FROM project.generation_requests"
                        + " WHERE generation_request_id = ?",
                (rs, n) -> new Row(rs.getString(1), rs.getString(2), rs.getObject(3, OffsetDateTime.class)),
                requestId);
    }

    private void payment(GenerationAdmission answer) {
        when(payment.admitGeneration(any(), any(), any())).thenReturn(answer);
    }

    private long events(UUID projectId) {
        return outbox.count(projectId.toString()).records();
    }

    @ParameterizedTest
    @CsvSource({"status = 'suspended'", "deleted_at = now()"})
    @org.junit.jupiter.api.DisplayName(
            "FR-38.7, FR-39.6: a project suspended or deleted while Payment is asked fails the request")
    void fr38_7_aProjectSuspendedOrDeletedDuringAdmissionFailsTheRequest(String change) {
        Captured c = captured();
        when(payment.admitGeneration(any(), any(), any())).thenAnswer(call -> {
            jdbc.update("UPDATE project.projects SET " + change + " WHERE project_id = ?", c.projectId());
            return GenerationAdmission.ADMITTED;
        });

        admissions.admit(c.requestId());

        assertThat(row(c.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "project_unavailable");
        assertThat(events(c.projectId())).as("no generation_requested event").isZero();
    }

    @Test
    void anAdmittedRequestIsGeneratingWithItsEventInOneTransaction() {
        Captured c = captured();
        payment(GenerationAdmission.ADMITTED);

        admissions.admit(c.requestId());

        assertThat(row(c.requestId())).extracting(Row::status, Row::failureCode).containsExactly("generating", null);
        verify(payment, times(1)).admitGeneration(c.owner(), c.projectId(), c.requestId());
        List<Map<String, Object>> records = db.sql(
                        "SELECT record_type, payload FROM " + OutboxRecords.TABLE + " WHERE record_key = ?")
                .param(c.projectId().toString())
                .query()
                .listOfRows();
        assertThat(records).hasSize(1);
        assertThat((String) records.getFirst().get("record_type")).endsWith(".GenerationRequested");
        JsonNode body = json.readTree((String) records.getFirst().get("payload"));
        assertThat(body.get("message_type").asString()).isEqualTo("project.generation_requested.v1");
        assertThat(body.get("schema_version").asInt()).isEqualTo(1);
        assertThat(body.get("project_id").asString()).isEqualTo(c.projectId().toString());
        assertThat(body.get("generation_request_id").asString())
                .isEqualTo(c.requestId().toString());
        assertThat(body.get("correlation_id").asString())
                .isEqualTo(c.requestId().toString());
        assertThat(body.get("captured_input_hash").asString()).isEqualTo(InputHasher.sha256(INPUT));
        assertThat(body.get("message_id").asString()).isNotBlank();
    }

    @ParameterizedTest
    @CsvSource({
        "TRIAL_ENDED, trial_ended",
        "PROJECT_NOT_ADMITTED, project_not_admitted",
        "PLAN_PAUSED, plan_paused",
        "'BAD REASON', admission_error"
    })
    void aRefusalFailsWithItsCode(String reason, String code) {
        Captured c = captured();
        payment(GenerationAdmission.refused(reason));

        admissions.admit(c.requestId());

        assertThat(row(c.requestId())).extracting(Row::status, Row::failureCode).containsExactly("failed", code);
        assertThat(events(c.projectId())).isZero();
    }

    @Test
    void anyOtherAnswerFailsAtOnceAsAdmissionError() {
        Captured c = captured();
        payment(GenerationAdmission.ERROR);

        admissions.admit(c.requestId());

        assertThat(row(c.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "admission_error");
        assertThat(events(c.projectId())).isZero();
    }

    @Test
    void unavailableIsRetriedUntilTheWindowEnds() {
        Captured c = captured();
        payment(GenerationAdmission.UNAVAILABLE);

        assertThatThrownBy(() -> admissions.admit(c.requestId())).isInstanceOf(AdmissionUnavailable.class);
        assertThat(row(c.requestId()).status()).isEqualTo("captured");

        CLOCK.advance(Duration.ofMinutes(14).plusSeconds(59));
        assertThatThrownBy(() -> admissions.admit(c.requestId())).isInstanceOf(AdmissionUnavailable.class);
        assertThat(row(c.requestId()).status()).isEqualTo("captured");

        CLOCK.advance(Duration.ofSeconds(1));
        admissions.admit(c.requestId());
        assertThat(row(c.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "admission_unavailable");
        verify(payment, times(3)).admitGeneration(c.owner(), c.projectId(), c.requestId());
        assertThat(events(c.projectId())).isZero();
    }

    @Test
    void theWindowStartsAtCapture() {
        Captured c = captured();
        CLOCK.advance(Duration.ofMinutes(20));
        payment(GenerationAdmission.UNAVAILABLE);

        admissions.admit(c.requestId());

        assertThat(row(c.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "admission_unavailable");
    }

    @Test
    void aRetryAfterAdmissionIsHarmless() {
        Captured c = captured();
        payment(GenerationAdmission.ADMITTED);
        admissions.admit(c.requestId());
        Row admitted = row(c.requestId());
        CLOCK.advance(Duration.ofMinutes(1));

        admissions.admit(c.requestId());

        assertThat(row(c.requestId())).isEqualTo(admitted);
        assertThat(events(c.projectId())).isEqualTo(1);
        verify(payment, times(1)).admitGeneration(any(), any(), any());
    }

    @Test
    void aRequestNoLongerCapturedEndsTheJobWithoutAWrite() {
        Captured c = captured();
        tx.executeWithoutResult(s -> assertThat(transitions.move(
                        c.requestId(),
                        GenerationRequestStatus.captured,
                        GenerationRequestStatus.failed,
                        "admission_error"))
                .isTrue());
        Row before = row(c.requestId());
        CLOCK.advance(Duration.ofMinutes(1));

        admissions.admit(c.requestId());

        assertThat(row(c.requestId())).isEqualTo(before);
        verifyNoInteractions(payment);
        assertThat(events(c.projectId())).isZero();
    }

    @Test
    void aProjectSuspendedDuringTheWindowFailsAsProjectUnavailable() {
        Captured c = captured();
        payment(GenerationAdmission.UNAVAILABLE);
        assertThatThrownBy(() -> admissions.admit(c.requestId())).isInstanceOf(AdmissionUnavailable.class);
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", c.projectId());

        admissions.admit(c.requestId());

        assertThat(row(c.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "project_unavailable");
        verify(payment, times(1)).admitGeneration(any(), any(), any());
    }

    @Test
    void aDeletedProjectFailsAsProjectUnavailable() {
        Captured c = captured();
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", c.projectId());

        admissions.admit(c.requestId());

        assertThat(row(c.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "project_unavailable");
        verifyNoInteractions(payment);
    }

    @Test
    void anUnknownRequestEndsTheJob() {
        admissions.admit(UUID.randomUUID());
        verifyNoInteractions(payment);
    }

    @Test
    void giveUpStoresTheRightCodeOnlyWhileCaptured() {
        Captured unavailable = captured();
        Captured error = captured();
        Captured generating = captured();
        tx.executeWithoutResult(s -> transitions.move(
                generating.requestId(), GenerationRequestStatus.captured, GenerationRequestStatus.generating, null));
        Row before = row(generating.requestId());
        CLOCK.advance(Duration.ofMinutes(1));

        admissions.giveUp(unavailable.requestId(), true);
        admissions.giveUp(error.requestId(), false);
        admissions.giveUp(generating.requestId(), true);

        assertThat(row(unavailable.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "admission_unavailable");
        assertThat(row(error.requestId()))
                .extracting(Row::status, Row::failureCode)
                .containsExactly("failed", "admission_error");
        assertThat(row(generating.requestId())).isEqualTo(before);
    }

    @Test
    void transitionsOutsideTheTableAreRefused() {
        Captured c = captured();
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> transitions.move(
                        c.requestId(), GenerationRequestStatus.captured, GenerationRequestStatus.published, null)))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> transitions.move(
                        c.requestId(), GenerationRequestStatus.captured, GenerationRequestStatus.failed, "x"))
                .as("a move needs the caller's transaction")
                .isInstanceOf(IllegalTransactionStateException.class);
        assertThat(row(c.requestId()).status()).isEqualTo("captured");
    }

    @Test
    void anEntityPersistsEveryColumnAndTheListsPageNewestFirst() {
        UUID owner = UUID.randomUUID();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Gen", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        UUID contractId = rows.contract(projectId, owner);
        UUID older = Uuids.v7();
        String requestHash = InputHasher.sha256("{\"generationContractId\":\"" + contractId + "\"}");
        tx.executeWithoutResult(s -> requests.saveAndFlush(GenerationRequestEntity.captured(
                older,
                projectId,
                "key-older",
                requestHash,
                contractId,
                0,
                INPUT,
                InputHasher.sha256(INPUT),
                CLOCK.instant())));

        GenerationRequestEntity stored = requests.findByProjectIdAndIdempotencyKey(projectId, "key-older")
                .orElseThrow();
        assertThat(stored.getId()).isEqualTo(older);
        assertThat(stored.isNew()).isFalse();
        assertThat(stored.projectId()).isEqualTo(projectId);
        assertThat(stored.requestHash()).isEqualTo(requestHash);
        assertThat(stored.generationContractId()).isEqualTo(contractId);
        assertThat(stored.capturedEditRevision()).isZero();
        assertThat(json.readTree(stored.capturedInput())).isEqualTo(json.readTree(INPUT));
        assertThat(stored.capturedInputHash()).isEqualTo(InputHasher.sha256(INPUT));
        assertThat(stored.acceptedGenerationRunId()).isNull();
        assertThat(stored.sourceCommitSha()).isNull();
        assertThat(stored.status()).isEqualTo(GenerationRequestStatus.captured);
        assertThat(stored.failureCode()).isNull();
        assertThat(stored.createdAt()).isEqualTo(CLOCK.instant());
        assertThat(stored.updatedAt()).isEqualTo(CLOCK.instant());

        tx.executeWithoutResult(s -> transitions.move(
                older, GenerationRequestStatus.captured, GenerationRequestStatus.failed, "admission_error"));
        CLOCK.advance(Duration.ofSeconds(1));
        UUID newer = rows.captured(projectId, contractId, INPUT);

        GenerationRequestEntity failed =
                requests.findByProjectIdAndGenerationRequestId(projectId, older).orElseThrow();
        assertThat(failed.status()).isEqualTo(GenerationRequestStatus.failed);
        assertThat(failed.failureCode()).isEqualTo("admission_error");
        assertThat(failed.updatedAt()).isEqualTo(CLOCK.instant().minusSeconds(1));
        assertThat(requests.findByProjectIdAndGenerationRequestId(UUID.randomUUID(), older))
                .isEmpty();

        assertThat(requests.firstPage(projectId, Limit.of(1)))
                .extracting(GenerationRequestEntity::generationRequestId)
                .containsExactly(newer);
        assertThat(requests.firstPage(projectId, Limit.of(5)))
                .extracting(GenerationRequestEntity::generationRequestId)
                .containsExactly(newer, older);
        GenerationRequestEntity first =
                requests.firstPage(projectId, Limit.of(1)).getFirst();
        assertThat(requests.pageAfter(projectId, first.createdAt(), first.generationRequestId(), Limit.of(5)))
                .extracting(GenerationRequestEntity::generationRequestId)
                .containsExactly(older);
    }
}
