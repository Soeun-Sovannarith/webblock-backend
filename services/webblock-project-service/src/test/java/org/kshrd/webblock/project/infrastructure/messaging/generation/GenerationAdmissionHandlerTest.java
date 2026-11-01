package org.kshrd.webblock.project.infrastructure.messaging.generation;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;

import io.namastack.outbox.handler.OutboxFailureContext;
import io.namastack.outbox.handler.OutboxRecordMetadata;
import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.config.GenerationProperties;
import org.kshrd.webblock.project.application.generation.handler.AdmissionUnavailable;
import org.kshrd.webblock.project.application.generation.handler.GenerationAdmissionService;
import org.kshrd.webblock.project.application.generation.job.GenerationAdmissionJob;

class GenerationAdmissionHandlerTest {

    static final UUID REQUEST = UUID.fromString("0199e0a2-0000-7000-8000-0000000000e1");

    final GenerationAdmissionService service = mock(GenerationAdmissionService.class);
    final GenerationAdmissionHandler handler = new GenerationAdmissionHandler(
            service, new GenerationProperties("1.0.0", Duration.ofMinutes(15), null, null));

    private static OutboxFailureContext failure(Throwable last) {
        return new OutboxFailureContext(
                "record", "generation:" + REQUEST, Instant.now(), 20, last, "handler", true, false, Map.of());
    }

    @Test
    void delaysAre5s10s30sThen60s() {
        OutboxRetryPolicy policy = handler.getRetryPolicy();
        assertThat(IntStream.rangeClosed(1, 6).mapToObj(policy::nextDelay))
                .containsExactly(
                        Duration.ofSeconds(5),
                        Duration.ofSeconds(10),
                        Duration.ofSeconds(30),
                        Duration.ofSeconds(60),
                        Duration.ofSeconds(60),
                        Duration.ofSeconds(60));
    }

    @Test
    void retriesOutlastTheWindow() {
        OutboxRetryPolicy policy = handler.getRetryPolicy();
        assertThat(policy.maxRetries()).isEqualTo(19);
        Duration spacing = IntStream.rangeClosed(1, policy.maxRetries())
                .mapToObj(policy::nextDelay)
                .reduce(Duration.ZERO, Duration::plus);
        assertThat(spacing).isEqualTo(Duration.ofSeconds(5 + 10 + 30 + 60 * (19 - 3)));
        assertThat(spacing).isGreaterThan(Duration.ofMinutes(15));
    }

    @Test
    void handleDelegatesToTheService() {
        handler.handle(
                new GenerationAdmissionJob(REQUEST),
                new OutboxRecordMetadata("generation:" + REQUEST, "handler", Instant.now(), Map.of(), 0));
        verify(service).admit(REQUEST);
        verifyNoMoreInteractions(service);
    }

    @Test
    void theFallbackGivesUpByTheLastFailure() {
        GenerationAdmissionJob job = new GenerationAdmissionJob(REQUEST);
        handler.handleFailure(job, failure(new AdmissionUnavailable(REQUEST)));
        verify(service).giveUp(REQUEST, true);
        handler.handleFailure(job, failure(new IllegalStateException("database down")));
        verify(service).giveUp(REQUEST, false);
        verifyNoMoreInteractions(service);
    }

    @Test
    void theJobKeyIsTheRequestsOwn() {
        assertThat(new GenerationAdmissionJob(REQUEST).outboxKey()).isEqualTo("generation:" + REQUEST);
    }
}
