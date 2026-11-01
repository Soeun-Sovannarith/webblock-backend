package org.kshrd.webblock.notification.infrastructure.messaging;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.common.domain.NotificationDeliveryException;
import org.kshrd.webblock.notification.common.domain.PermanentNotificationDeliveryException;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.listener.ListenerExecutionFailedException;
import org.springframework.amqp.support.converter.MessageConversionException;

import static org.assertj.core.api.Assertions.assertThat;

class ListenerRetryConfigTest {

    private static ListenerExecutionFailedException wrapped(Throwable cause) {
        // The listener adapter wraps whatever the consumer throws
        return new ListenerExecutionFailedException("Listener threw exception", cause);
    }

    @Test
    @DisplayName("doesNotRetryAPermanentFailureEvenWhenTheListenerWrapsIt")
    void permanentFailure() {
        assertThat(ListenerRetryConfig.isRetryable(new PermanentNotificationDeliveryException("404", new RuntimeException()))).isFalse();
        assertThat(ListenerRetryConfig.isRetryable(wrapped(new PermanentNotificationDeliveryException("404", new RuntimeException())))).isFalse();
    }

    @Test
    @DisplayName("doesNotRetryAnEventThatFailedValidation")
    void validationFailure() {
        assertThat(ListenerRetryConfig.isRetryable(wrapped(new AmqpRejectAndDontRequeueException("Invalid event")))).isFalse();
    }

    @Test
    @DisplayName("doesNotRetryAMessageBodyThatCannotBeRead")
    void unreadableBody() {
        assertThat(ListenerRetryConfig.isRetryable(wrapped(new MessageConversionException("not json")))).isFalse();
    }

    @Test
    @DisplayName("retriesTemporaryAndUnknownFailures")
    void temporaryFailure() {
        assertThat(ListenerRetryConfig.isRetryable(wrapped(new NotificationDeliveryException("503", new RuntimeException())))).isTrue();
        assertThat(ListenerRetryConfig.isRetryable(wrapped(new IllegalStateException("project-service down")))).isTrue();
        assertThat(ListenerRetryConfig.isRetryable(new RuntimeException())).isTrue();
    }

    @Test
    @DisplayName("stopsLookingAtAnEndlessCauseChain")
    void causeLoop() {
        var a = new RuntimeException("a");
        var b = new RuntimeException("b", a);
        a.initCause(b);

        assertThat(ListenerRetryConfig.isRetryable(a)).isTrue();
    }
}
