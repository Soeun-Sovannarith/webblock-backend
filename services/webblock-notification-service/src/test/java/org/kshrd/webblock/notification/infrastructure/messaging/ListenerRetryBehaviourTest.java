package org.kshrd.webblock.notification.infrastructure.messaging;

import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.application.notification.handler.HandlePublishCompletedHandler;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.common.domain.NotificationDeliveryException;
import org.kshrd.webblock.notification.common.domain.PermanentNotificationDeliveryException;
import org.kshrd.webblock.notification.presentation.messaging.EventPayloadValidator;
import org.mockito.Mockito;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.listener.AbstractMessageListenerContainer;
import org.springframework.amqp.rabbit.listener.RabbitListenerEndpointRegistry;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.catchThrowable;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * Runs a message through the listener container Spring really builds for the publish_completed queue:
 * the real retry advice from application.yml, the real recoverer, the real @RabbitListener adapter, the real consumer
 * and the real validator. Only the handler is a mock, and no RabbitMQ broker is needed (the containers are not started).
 * What this does NOT prove: that the broker routes a rejected message to the ".dlq" queue (see RabbitMQConfigTest for the
 * queue arguments). Only the retry delay is shortened; the attempt count is the one configured in application.yml.
 */
@SpringBootTest(properties = {
        "eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "knock.api-key=dummy-key-not-a-real-credential",
        "spring.rabbitmq.listener.simple.auto-startup=false",
        "spring.rabbitmq.listener.simple.retry.initial-interval=1ms",
        "spring.rabbitmq.listener.simple.retry.max-interval=2ms"})
class ListenerRetryBehaviourTest {

    /** Total tries (first call + retries) configured in application.yml: spring.rabbitmq.listener.simple.retry.max-attempts. */
    private static final int CONFIGURED_ATTEMPTS = 5;

    @Autowired
    private RabbitListenerEndpointRegistry registry;
    @Autowired
    private org.springframework.core.env.Environment environment;
    @MockitoBean
    private HandlePublishCompletedHandler handler;
    @MockitoSpyBean
    private EventPayloadValidator validator;

    private AbstractMessageListenerContainer container;
    private final Channel channel = mock(Channel.class);

    @BeforeEach
    void findTheContainerOfTheQueue() {
        container = registry.getListenerContainers().stream()
                .filter(AbstractMessageListenerContainer.class::isInstance)
                .map(AbstractMessageListenerContainer.class::cast)
                .filter(c -> c.getQueueNames() != null
                        && java.util.Arrays.asList(c.getQueueNames()).contains(RabbitMQConfig.PUBLISH_COMPLETED_QUEUE))
                .findFirst()
                .orElseThrow(() -> new AssertionError("no listener container for " + RabbitMQConfig.PUBLISH_COMPLETED_QUEUE));
        Mockito.reset(handler, validator);
    }

    private static Message message(String json) {
        MessageProperties props = new MessageProperties();
        props.setContentType(MessageProperties.CONTENT_TYPE_JSON);
        props.setDeliveryTag(1L);
        return MessageBuilder.withBody(json.getBytes(StandardCharsets.UTF_8)).andProperties(props).build();
    }

    private static String validEvent() {
        return """
                {"message_id":"%s","project_id":"%s","release_id":"%s","status":"LIVE","event_key":"k-1"}
                """.formatted(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID());
    }

    /**
     * Hands the message to the container's advice chain the way the broker consumer does. "invokeListener" is the step
     * after the "is the container running" check, which would refuse a message here because the container is not started.
     */
    private Throwable deliver(Message message) {
        return catchThrowable(() -> ReflectionTestUtils.invokeMethod(container, "invokeListener", channel, message));
    }

    private static boolean rejectsWithoutRequeue(Throwable thrown) {
        for (Throwable t = thrown; t != null; t = t.getCause()) {
            if (t instanceof AmqpRejectAndDontRequeueException) {
                return true;
            }
        }
        return false;
    }

    @Test
    @DisplayName("usesTheRetryKeysSpringBoot4ReadsNotTheIgnoredMaxAttempts")
    void retryKeysAreTheOnesBootReads() {
        // Boot 4 ignores spring.rabbitmq.listener.simple.retry.max-attempts without any warning; max-retries is the live key.
        assertThat(environment.containsProperty("spring.rabbitmq.listener.simple.retry.max-attempts")).isFalse();
        assertThat(environment.getProperty("spring.rabbitmq.listener.simple.retry.max-retries", Integer.class))
                .isEqualTo(CONFIGURED_ATTEMPTS - 1);
    }

    @Test
    @DisplayName("callsTheHandlerOnceAndRaisesNothingForAGoodMessage")
    void goodMessage() {
        doNothing().when(handler).handle(any());

        assertThat(deliver(message(validEvent()))).isNull();

        verify(handler, times(1)).handle(any(PublishCompletedEvent.class));
    }

    @Test
    @DisplayName("sendsAMalformedMessageToTheDeadLetterQueueWithoutRetryingIt")
    void malformedMessageIsNotRetried() {
        String missingStatus = """
                {"message_id":"%s","project_id":"%s","release_id":"%s","event_key":"k-1"}
                """.formatted(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID());

        Throwable thrown = deliver(message(missingStatus));

        assertThat(rejectsWithoutRequeue(thrown)).as("rejected without requeue (dead-lettered)").isTrue();
        verify(validator, times(1)).validate(any());
        verify(handler, never()).handle(any());
    }

    @Test
    @DisplayName("sendsAPermanentFailureToTheDeadLetterQueueWithoutRetryingIt")
    void permanentFailureIsNotRetried() {
        doThrow(new PermanentNotificationDeliveryException("Knock said 404", new RuntimeException()))
                .when(handler).handle(any());

        Throwable thrown = deliver(message(validEvent()));

        verify(handler, times(1)).handle(any());
        assertThat(rejectsWithoutRequeue(thrown)).as("rejected without requeue (dead-lettered)").isTrue();
    }

    @Test
    @DisplayName("retriesATemporaryFailureAFixedNumberOfTimesThenDeadLettersIt")
    void temporaryFailureIsRetriedWithABound() {
        doThrow(new NotificationDeliveryException("Knock said 503", new RuntimeException()))
                .when(handler).handle(any());

        Throwable thrown = deliver(message(validEvent()));

        verify(handler, times(CONFIGURED_ATTEMPTS)).handle(any());
        assertThat(rejectsWithoutRequeue(thrown)).as("rejected without requeue (dead-lettered)").isTrue();
    }

    @Test
    @DisplayName("deliversTheMessageWhenATemporaryFailureClearsBeforeTheLimit")
    void temporaryFailureThatClearsIsDelivered() {
        doThrow(new NotificationDeliveryException("Knock said 503", new RuntimeException()))
                .doThrow(new NotificationDeliveryException("Knock said 503", new RuntimeException()))
                .doNothing()
                .when(handler).handle(any());

        assertThatCode(() -> {
            Throwable thrown = deliver(message(validEvent()));
            if (thrown != null) {
                throw new AssertionError("expected delivery after retries but got " + thrown, thrown);
            }
        }).doesNotThrowAnyException();

        verify(handler, times(3)).handle(any());
    }
}
