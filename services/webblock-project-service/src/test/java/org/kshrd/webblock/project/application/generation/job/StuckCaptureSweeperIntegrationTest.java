package org.kshrd.webblock.project.application.generation.job;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.MutableClock;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionTemplate;

/** FR-28: the sweeper fails requests stuck in captured past the admission window plus the margin (15m + 5m). */
@ServiceIntegrationTest
@DirtiesContext
@Import(StuckCaptureSweeperIntegrationTest.Clocks.class)
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=8",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class StuckCaptureSweeperIntegrationTest {

    @TestConfiguration(proxyBeanMethods = false)
    static class Clocks {
        @Bean
        @Primary
        MutableClock mutableClock() {
            return new MutableClock();
        }
    }

    @MockitoBean
    PaymentAdmissions payment;

    @Autowired
    StuckCaptureSweeper sweeper;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    MutableClock clock;

    @Autowired
    MockMvc mvc;

    private record Owned(UUID owner, UUID projectId) {}

    private Owned project() {
        UUID owner = Uuids.v7();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Sweep", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        return new Owned(owner, projectId);
    }

    private UUID capturedRequest(Owned p) {
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID contract = rows.contract(p.projectId(), p.owner());
        return rows.captured(p.projectId(), contract, "{\"pages\":[]}");
    }

    private String statusOf(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT status FROM project.generation_requests WHERE generation_request_id = ?",
                String.class,
                requestId);
    }

    private String failureCodeOf(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT failure_code FROM project.generation_requests WHERE generation_request_id = ?",
                String.class,
                requestId);
    }

    @Test
    void fr28_1_aRequestCapturedTwentyOneMinutesAgoFailsAndTheWebsiteCapturesAgain() throws Exception {
        when(payment.admitGeneration(any(), any(), any())).thenReturn(GenerationAdmission.refused("NO_ENTITLEMENT"));
        Owned p = project();
        UUID stuck = capturedRequest(p);
        clock.advance(Duration.ofMinutes(21));

        sweeper.sweep();

        assertThat(statusOf(stuck)).isEqualTo("failed");
        assertThat(failureCodeOf(stuck)).isEqualTo("admission_unavailable");
        UUID contract = new GenerationRows(jdbc, clock).contract(p.projectId(), p.owner());
        mvc.perform(post("/api/v1/projects/{projectId}/generation-requests", p.projectId())
                        .with(Users.user(p.owner()))
                        .header("Idempotency-Key", "after-sweep")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"generationContractId\":\"" + contract + "\"}"))
                .andExpect(status().isAccepted());
    }

    @Test
    void fr28_2_aRequestCapturedTenMinutesAgoStaysCaptured() {
        Owned p = project();
        UUID young = capturedRequest(p);
        clock.advance(Duration.ofMinutes(10));

        sweeper.sweep();

        assertThat(statusOf(young)).isEqualTo("captured");
        assertThat(failureCodeOf(young)).isNull();
    }

    @Test
    void fr28_2_aRequestInGeneratingIsNeverTouched() {
        Owned p = project();
        UUID request = capturedRequest(p);
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'generating' WHERE generation_request_id = ?",
                request);
        clock.advance(Duration.ofMinutes(60));

        sweeper.sweep();

        assertThat(statusOf(request)).isEqualTo("generating");
        assertThat(failureCodeOf(request)).isNull();
    }

    @Test
    void fr28_1_twoConcurrentSweepsFailEachRequestOnce() throws Exception {
        Owned p = project();
        UUID stuck = capturedRequest(p);
        clock.advance(Duration.ofMinutes(30));
        Instant cutoff = clock.instant().minus(Duration.ofMinutes(20));
        Integer waiting = jdbc.queryForObject(
                "SELECT count(*) FROM project.generation_requests WHERE status = 'captured' AND created_at < ?",
                Integer.class,
                Timestamp.from(cutoff));
        assertThat(waiting).isGreaterThanOrEqualTo(1);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            CountDownLatch go = new CountDownLatch(1);
            Callable<Integer> run = () -> {
                go.await();
                return sweeper.sweep();
            };
            Future<Integer> a = pool.submit(run);
            Future<Integer> b = pool.submit(run);
            go.countDown();
            assertThat(a.get() + b.get()).isEqualTo(waiting);
        } finally {
            pool.shutdownNow();
        }
        assertThat(statusOf(stuck)).isEqualTo("failed");
        assertThat(failureCodeOf(stuck)).isEqualTo("admission_unavailable");
    }
}
