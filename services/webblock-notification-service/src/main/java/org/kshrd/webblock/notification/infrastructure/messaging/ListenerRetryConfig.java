package org.kshrd.webblock.notification.infrastructure.messaging;

import org.kshrd.webblock.notification.common.domain.PermanentFailure;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.support.converter.MessageConversionException;
import org.springframework.boot.amqp.autoconfigure.RabbitListenerRetrySettingsCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Adds one rule to the listener retry that Spring Boot already builds from spring.rabbitmq.listener.simple.retry:
 * a failure that retrying cannot fix is not retried. It goes straight to the dead-letter queue.
 * Everything else (Knock 429/5xx, network errors, a lookup that is briefly down) keeps the bounded retry.
 * The number of tries, the delays and the "reject without requeue" step after the last try stay in application.yml / Boot.
 */
@Configuration
public class ListenerRetryConfig {

    private static final int MAX_CAUSE_DEPTH = 10;

    @Bean
    RabbitListenerRetrySettingsCustomizer permanentFailuresAreNotRetried() {
        return settings -> settings.setExceptionPredicate(ListenerRetryConfig::isRetryable);
    }

    /**
     * The listener adapter wraps what the consumer throws, so the whole cause chain is checked.
     *
     * @return false for: a {@link PermanentFailure}, an event that failed validation ({@link AmqpRejectAndDontRequeueException}),
     * or a body that cannot be read ({@link MessageConversionException}); true for everything else
     */
    static boolean isRetryable(Throwable error) {
        Throwable current = error;
        for (int depth = 0; current != null && depth < MAX_CAUSE_DEPTH; depth++) {
            if (current instanceof PermanentFailure
                    || current instanceof AmqpRejectAndDontRequeueException
                    || current instanceof MessageConversionException) {
                return false;
            }
            current = current.getCause();
        }
        return true;
    }
}
