package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import io.namastack.outbox.handler.OutboxRecordMetadata;
import io.namastack.outbox.handler.OutboxTypedHandler;
import io.namastack.outbox.retry.OutboxRetryAware;
import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * A local outbox job handler that fails the first {@code failuresBeforeSuccess} attempts of each job. It declares its
 * own retry policy (fixed backoff), as a feature's local job would.
 */
class FlakyJobHandler implements OutboxTypedHandler<ProbeJob>, OutboxRetryAware {

    static final Duration BACKOFF = Duration.ofSeconds(2);

    private final Map<UUID, List<Instant>> attempts = new ConcurrentHashMap<>();

    @Override
    public void handle(ProbeJob job, OutboxRecordMetadata metadata) {
        List<Instant> seen = attempts.computeIfAbsent(job.jobId(), id -> new CopyOnWriteArrayList<>());
        seen.add(Instant.now());
        if (seen.size() <= job.failuresBeforeSuccess()) {
            throw new IllegalStateException("planned failure " + seen.size() + " of job " + job.jobId());
        }
    }

    @Override
    public OutboxRetryPolicy getRetryPolicy() {
        return OutboxRetryPolicy.builder().maxRetries(5).fixedBackOff(BACKOFF).build();
    }

    List<Instant> attemptsOf(UUID jobId) {
        return List.copyOf(attempts.getOrDefault(jobId, List.of()));
    }
}
