package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import io.namastack.outbox.Outbox;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.amqp.autoconfigure.RabbitTemplateCustomizer;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.Bean;
import org.springframework.jdbc.core.simple.JdbcClient;

/** Test-only beans of spike S1, added to each application context the spike starts. */
@TestConfiguration(proxyBeanMethods = false)
class OutboxSpikeBeans {

    /** Every publish attempt seen by any spike context's RabbitTemplate: message_id to the instances that sent it. */
    static final Map<String, List<String>> PUBLISH_ATTEMPTS = new ConcurrentHashMap<>();

    @Bean
    ProbeService probeService(ApplicationEventPublisher events, Outbox outbox, JdbcClient jdbc) {
        return new ProbeService(events, outbox, jdbc);
    }

    @Bean
    FlakyJobHandler flakyJobHandler() {
        return new FlakyJobHandler();
    }

    /**
     * Records which instance sent which message (instrumentation only; the message is not changed). A positive {@code
     * spike.publish-delay-ms} slows every send, which widens any window in which two instances could send the same
     * partition.
     */
    @Bean
    RabbitTemplateCustomizer publishRecorder(
            @Value("${spike.instance:main}") String instance, @Value("${spike.publish-delay-ms:0}") long delayMillis) {
        return template -> template.addBeforePublishPostProcessors(message -> {
            String id = message.getMessageProperties().getMessageId();
            if (id != null) {
                PUBLISH_ATTEMPTS
                        .computeIfAbsent(id, key -> new CopyOnWriteArrayList<>())
                        .add(instance);
            }
            if (delayMillis > 0) {
                try {
                    Thread.sleep(delayMillis);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            return message;
        });
    }
}
