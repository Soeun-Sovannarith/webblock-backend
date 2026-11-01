package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import io.namastack.outbox.Outbox;
import java.sql.Timestamp;
import java.time.Instant;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.annotation.Transactional;

/**
 * Publishes the spike's events the way a feature service will: inside its own transaction, next to a business write
 * (here an inbox row stands in for the business change).
 */
public class ProbeService {

    private final ApplicationEventPublisher events;
    private final Outbox outbox;
    private final JdbcClient jdbc;

    ProbeService(ApplicationEventPublisher events, Outbox outbox, JdbcClient jdbc) {
        this.events = events;
        this.outbox = outbox;
        this.jdbc = jdbc;
    }

    @Transactional
    public void publish(ProbeHappened event) {
        businessWrite(event);
        events.publishEvent(event);
    }

    @Transactional
    public void publishThenFail(ProbeHappened event) {
        businessWrite(event);
        events.publishEvent(event);
        throw new IllegalStateException("the business operation fails after publishing");
    }

    @Transactional
    public void schedule(ProbeJob job) {
        outbox.schedule(job, job.jobId().toString());
    }

    private void businessWrite(ProbeHappened event) {
        jdbc.sql("INSERT INTO project_messaging.processed_messages (message_id, message_type, outcome, processed_at) "
                        + "VALUES (?, ?, 'spike', ?)")
                .params(event.messageId(), event.messageType(), Timestamp.from(Instant.now()))
                .update();
    }
}
