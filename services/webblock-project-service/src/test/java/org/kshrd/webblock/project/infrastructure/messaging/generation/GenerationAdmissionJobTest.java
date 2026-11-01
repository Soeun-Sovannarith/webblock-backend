package org.kshrd.webblock.project.infrastructure.messaging.generation;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import io.namastack.outbox.Outbox;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.handler.GenerationAdmissionService;
import org.kshrd.webblock.project.application.generation.job.GenerationAdmissionJob;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The admission job through Namastack itself, with the prototype Payment stand-in. The handler bean is replaced by
 * one with a single retry 100 ms apart, so a job whose Payment stays unavailable exhausts its retries within the test
 * (the window is still open, so every attempt throws AdmissionUnavailable). The stand-in reads its refusal file on
 * every call: an empty object admits, anything unparsable is UNAVAILABLE.
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.main.allow-bean-definition-overriding=true",
            "spring.datasource.hikari.maximum-pool-size=4",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class GenerationAdmissionJobTest {

    static Path standInFile;

    @DynamicPropertySource
    static void standIn(DynamicPropertyRegistry registry) throws Exception {
        standInFile = Files.createTempFile("generation-refusals", ".json");
        standInFile.toFile().deleteOnExit();
        registry.add("webblock.payment.stand-in-file", standInFile::toString);
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class QuickRetries {

        /** Same bean name as the scanned handler, so it replaces it. */
        @Bean
        GenerationAdmissionHandler generationAdmissionHandler(GenerationAdmissionService service) {
            return new GenerationAdmissionHandler(service, 1, failureCount -> Duration.ofMillis(100));
        }
    }

    @Autowired
    Outbox outbox;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Clock clock;

    @Autowired
    GenerationAdmissionHandler handler;

    GenerationRows rows;

    @BeforeEach
    void rows() {
        rows = new GenerationRows(jdbc, clock);
    }

    private UUID captured() {
        UUID owner = UUID.randomUUID();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Job", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        return rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
    }

    private void schedule(UUID requestId) {
        GenerationAdmissionJob job = new GenerationAdmissionJob(requestId);
        tx.executeWithoutResult(s -> outbox.schedule(job, job.outboxKey()));
    }

    private String statusAndCode(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT status || '/' || coalesce(failure_code, '-') FROM project.generation_requests"
                        + " WHERE generation_request_id = ?",
                String.class,
                requestId);
    }

    private String recordStatus(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT status FROM " + OutboxRecords.TABLE + " WHERE record_key = ?",
                String.class,
                "generation:" + requestId);
    }

    @Test
    void theScheduledJobRunsAndAdmits() throws Exception {
        Files.writeString(standInFile, "{}");
        UUID requestId = captured();

        schedule(requestId);

        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> {
            assertThat(statusAndCode(requestId)).isEqualTo("generating/-");
            assertThat(recordStatus(requestId)).isEqualTo(OutboxRecords.COMPLETED);
        });
    }

    @Test
    void exhaustedRetriesCallTheFallback() throws Exception {
        assertThat(handler.getRetryPolicy().maxRetries())
                .as("the replacement handler is the one in the context")
                .isEqualTo(1);
        Files.writeString(standInFile, "not json at all");
        UUID requestId = captured();

        schedule(requestId);

        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> {
            assertThat(statusAndCode(requestId)).isEqualTo("failed/admission_unavailable");
            // A fallback that succeeds completes the record (Namastack 1.7.3 FallbackOutboxRecordProcessor).
            assertThat(recordStatus(requestId)).isEqualTo(OutboxRecords.COMPLETED);
        });
        assertThat(jdbc.queryForObject(
                        "SELECT failure_count FROM " + OutboxRecords.TABLE + " WHERE record_key = ?",
                        Integer.class,
                        "generation:" + requestId))
                .as("the first attempt and its one retry both failed before the fallback ran")
                .isEqualTo(2);
    }
}
