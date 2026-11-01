package org.kshrd.webblock.project.infrastructure.messaging.generation;

import io.namastack.outbox.handler.OutboxFailureContext;
import io.namastack.outbox.handler.OutboxRecordMetadata;
import io.namastack.outbox.handler.OutboxTypedHandlerWithFallback;
import io.namastack.outbox.retry.BackOffStrategy;
import io.namastack.outbox.retry.OutboxRetryAware;
import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.time.Duration;
import org.kshrd.webblock.project.application.generation.config.GenerationProperties;
import org.kshrd.webblock.project.application.generation.handler.AdmissionUnavailable;
import org.kshrd.webblock.project.application.generation.handler.GenerationAdmissionService;
import org.kshrd.webblock.project.application.generation.job.GenerationAdmissionJob;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Runs a request's Payment admission job (plan decision P3). The service decides the window from the request's
 * created_at; Namastack only spaces the attempts, 5 s, 10 s, 30 s and then every 60 s, with enough retries to outlast
 * the window. Once they are spent, or on a failure the policy does not retry, the fallback stores a failure code, so
 * no request stays captured.
 */
@Component
public class GenerationAdmissionHandler
        implements OutboxTypedHandlerWithFallback<GenerationAdmissionJob>, OutboxRetryAware {

    private final GenerationAdmissionService service;
    private final OutboxRetryPolicy retryPolicy;

    @Autowired
    public GenerationAdmissionHandler(GenerationAdmissionService service, GenerationProperties properties) {
        this(service, maxRetries(properties.admissionWindow()), GenerationAdmissionHandler::delay);
    }

    /** For tests that need the retries spent quickly. */
    GenerationAdmissionHandler(GenerationAdmissionService service, int maxRetries, BackOffStrategy backOff) {
        this.service = service;
        this.retryPolicy = OutboxRetryPolicy.builder()
                .maxRetries(maxRetries)
                .backOff(backOff)
                .build();
    }

    /** More attempts than the window holds at one a minute, so the window, not the count, ends the retries. */
    private static int maxRetries(Duration window) {
        return (int) Math.ceil(window.toSeconds() / 60.0) + 4;
    }

    private static Duration delay(int failureCount) {
        return switch (failureCount) {
            case 1 -> Duration.ofSeconds(5);
            case 2 -> Duration.ofSeconds(10);
            case 3 -> Duration.ofSeconds(30);
            default -> Duration.ofSeconds(60);
        };
    }

    @Override
    public void handle(GenerationAdmissionJob job, OutboxRecordMetadata metadata) {
        service.admit(job.generationRequestId());
    }

    @Override
    public void handleFailure(GenerationAdmissionJob job, OutboxFailureContext context) {
        service.giveUp(job.generationRequestId(), context.getLastFailure() instanceof AdmissionUnavailable);
    }

    @Override
    public OutboxRetryPolicy getRetryPolicy() {
        return retryPolicy;
    }
}
