package org.kshrd.webblock.notification.infrastructure.observability;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.config.ContainerCustomizer;
import org.springframework.amqp.rabbit.listener.SimpleMessageListenerContainer;
import org.springframework.amqp.rabbit.retry.MessageRecoverer;
import org.springframework.amqp.rabbit.retry.RejectAndDontRequeueRecoverer;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class ListenerObservabilityConfigTest {

    private final SimpleMeterRegistry registry = new SimpleMeterRegistry();
    private final NotificationMetrics metrics = new NotificationMetrics(registry);
    private final ListenerObservabilityConfig config = new ListenerObservabilityConfig();

    private static Message messageFromQueue(String queue) {
        MessageProperties props = new MessageProperties();
        props.setConsumerQueue(queue);
        return MessageBuilder.withBody("{}".getBytes(StandardCharsets.UTF_8)).andProperties(props).build();
    }

    @Test
    @DisplayName("countsTheDeadLetteredMessageAndStillRejectsItWithoutRequeueLikeBootsOwnRecoverer")
    void recovererCountsAndStillRejects() {
        MessageRecoverer recoverer = config.countingRejectRecoverer(metrics);
        Message message = messageFromQueue(RabbitMQConfig.PAYMENT_DUE_QUEUE);

        assertThat(recoverer).isInstanceOf(RejectAndDontRequeueRecoverer.class);
        // Boot's recoverer throws a ListenerExecutionFailedException whose cause is the reject-without-requeue exception
        assertThatThrownBy(() -> recoverer.recover(message, new IllegalStateException("boom")))
                .hasCauseInstanceOf(AmqpRejectAndDontRequeueException.class);

        assertThat(registry.get(NotificationMetrics.EVENTS_DEAD_LETTERED)
                .tag("queue", RabbitMQConfig.PAYMENT_DUE_QUEUE).counter().count()).isEqualTo(1);
    }

    @Test
    @DisplayName("countsEveryDeliveredMessageAndPassesItOnUnchanged")
    void receivedMessagesAreCountedAndUntouched() {
        ContainerCustomizer<SimpleMessageListenerContainer> customizer = config.countReceivedEvents(metrics);
        SimpleMessageListenerContainer container = mock(SimpleMessageListenerContainer.class);

        customizer.configure(container);

        ArgumentCaptor<MessagePostProcessor> captured = ArgumentCaptor.forClass(MessagePostProcessor.class);
        verify(container).addAfterReceivePostProcessors(captured.capture());
        Message message = messageFromQueue(RabbitMQConfig.SOURCE_PUBLISHED_QUEUE);
        Message returned = captured.getValue().postProcessMessage(message);

        assertThat(returned).isSameAs(message);
        assertThat(registry.get(NotificationMetrics.EVENTS_RECEIVED)
                .tag("queue", RabbitMQConfig.SOURCE_PUBLISHED_QUEUE).counter().count()).isEqualTo(1);
    }
}
