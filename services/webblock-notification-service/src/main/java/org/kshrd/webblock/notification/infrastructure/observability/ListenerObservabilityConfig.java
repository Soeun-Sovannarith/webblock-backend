package org.kshrd.webblock.notification.infrastructure.observability;

import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.config.ContainerCustomizer;
import org.springframework.amqp.rabbit.listener.SimpleMessageListenerContainer;
import org.springframework.amqp.rabbit.retry.MessageRecoverer;
import org.springframework.amqp.rabbit.retry.RejectAndDontRequeueRecoverer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Counts what happens at the RabbitMQ listeners. It changes no behaviour:
 * - every delivered message is counted per queue, then handed on unchanged;
 * - the recoverer is Spring Boot's own {@link RejectAndDontRequeueRecoverer} (what Boot uses when no recoverer bean exists)
 *   plus one counter, so a rejected message still goes to the dead-letter queue exactly as before.
 */
@Configuration
public class ListenerObservabilityConfig {

    @Bean
    ContainerCustomizer<SimpleMessageListenerContainer> countReceivedEvents(NotificationMetrics metrics) {
        return container -> container.addAfterReceivePostProcessors(message -> {
            metrics.eventReceived(message.getMessageProperties().getConsumerQueue());
            return message;
        });
    }

    @Bean
    MessageRecoverer countingRejectRecoverer(NotificationMetrics metrics) {
        return new CountingRejectRecoverer(metrics);
    }

    /** Rejects the message without requeue (so RabbitMQ dead-letters it), after counting it. */
    static final class CountingRejectRecoverer extends RejectAndDontRequeueRecoverer {

        private final NotificationMetrics metrics;

        CountingRejectRecoverer(NotificationMetrics metrics) {
            this.metrics = metrics;
        }

        @Override
        public void recover(Message message, Throwable cause) {
            metrics.eventDeadLettered(message.getMessageProperties().getConsumerQueue());
            super.recover(message, cause);
        }
    }
}
