package org.kshrd.webblock.notification.infrastructure.observability;

import io.micrometer.observation.Observation;
import io.micrometer.observation.ObservationRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.common.presentation.TraceContext;
import org.slf4j.MDC;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.support.micrometer.RabbitMessageReceiverContext;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Uses a real ObservationRegistry, the way Spring AMQP drives the handler for each message it processes. */
class TraceContextObservationHandlerTest {

    private static final String TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";
    private static final String TRACEPARENT = "00-" + TRACE_ID + "-00f067aa0ba902b7-01";

    private final ObservationRegistry registry = ObservationRegistry.create();

    {
        registry.observationConfig().observationHandler(new TraceContextObservationHandler());
    }

    @AfterEach
    void cleanUp() {
        MDC.clear();
    }

    private static Message message(String key, Object value) {
        MessageProperties props = new MessageProperties();
        if (key != null) {
            props.setHeader(key, value);
        }
        return MessageBuilder.withBody("{}".getBytes(StandardCharsets.UTF_8)).andProperties(props).build();
    }

    private AtomicReference<String> observe(Message message, String requestIdKey) {
        AtomicReference<String> seen = new AtomicReference<>();
        Observation.createNotStarted("spring.rabbit.listener", () -> new RabbitMessageReceiverContext(message, "listener-1"), registry)
                .observe(() -> seen.set(MDC.get(requestIdKey)));
        return seen;
    }

    @Test
    @DisplayName("putsTheMessagesTraceIntoTheLogContextWhileItIsHandled")
    void messageTraceIsInTheLogContext() {
        MessageProperties props = new MessageProperties();
        props.setHeader("traceparent", TRACEPARENT);
        props.setHeader("x-request-id", "req-evt-1");
        Message message = MessageBuilder.withBody("{}".getBytes(StandardCharsets.UTF_8)).andProperties(props).build();
        AtomicReference<String> traceId = new AtomicReference<>();
        AtomicReference<String> requestId = new AtomicReference<>();

        Observation.createNotStarted("spring.rabbit.listener", () -> new RabbitMessageReceiverContext(message, "listener-1"), registry)
                .observe(() -> {
                    traceId.set(MDC.get(TraceContext.TRACE_ID));
                    requestId.set(MDC.get(TraceContext.REQUEST_ID));
                });

        assertThat(traceId.get()).isEqualTo(TRACE_ID);
        assertThat(requestId.get()).isEqualTo("req-evt-1");
        assertThat(MDC.getCopyOfContextMap()).as("removed once the message is done").isNullOrEmpty();
    }

    @Test
    @DisplayName("removesTheContextEvenWhenHandlingTheMessageFails")
    void contextIsRemovedAfterAFailure() {
        Message message = message("traceparent", TRACEPARENT);

        assertThatThrownBy(() -> Observation.createNotStarted("spring.rabbit.listener",
                () -> new RabbitMessageReceiverContext(message, "listener-1"), registry)
                .observe(() -> {
                    throw new IllegalStateException("handler failed");
                })).isInstanceOf(IllegalStateException.class);

        assertThat(MDC.getCopyOfContextMap()).isNullOrEmpty();
    }

    @Test
    @DisplayName("createsARequestIdAndNoTraceForAMessageWithoutHeaders")
    void messageWithoutHeaders() {
        AtomicReference<String> requestId = observe(message(null, null), TraceContext.REQUEST_ID);
        AtomicReference<String> traceId = observe(message(null, null), TraceContext.TRACE_ID);

        assertThat(requestId.get()).isNotBlank();
        assertThat(traceId.get()).isNull();
    }

    @Test
    @DisplayName("ignoresAHeaderThatIsNotText")
    void nonTextHeaderIsIgnored() {
        AtomicReference<String> traceId = observe(message("traceparent", 12345), TraceContext.TRACE_ID);

        assertThat(traceId.get()).isNull();
    }

    @Test
    @DisplayName("onlyHandlesObservationsOfReceivedRabbitMessages")
    void otherObservationsAreLeftAlone() {
        AtomicReference<String> requestId = new AtomicReference<>();

        Observation.createNotStarted("something.else", registry).observe(() -> requestId.set(MDC.get(TraceContext.REQUEST_ID)));

        assertThat(requestId.get()).isNull();
    }
}
