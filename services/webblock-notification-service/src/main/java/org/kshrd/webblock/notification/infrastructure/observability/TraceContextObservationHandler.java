package org.kshrd.webblock.notification.infrastructure.observability;

import io.micrometer.observation.Observation;
import io.micrometer.observation.ObservationHandler;
import org.kshrd.webblock.notification.common.presentation.TraceContext;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.support.micrometer.RabbitMessageReceiverContext;
import org.springframework.stereotype.Component;

/**
 * Spring AMQP processes every message inside a Micrometer observation (spring.rabbitmq.listener.simple.observation-enabled).
 * While that observation's scope is open, the message's {@code traceparent} and {@code x-request-id} headers are the current
 * trace context, so every log line written while handling the message (retries included) carries them. When the scope
 * closes the context is removed, so nothing leaks into the next message on the same thread.
 */
@Component
public class TraceContextObservationHandler implements ObservationHandler<RabbitMessageReceiverContext> {

    private static final String SCOPE_KEY = TraceContextObservationHandler.class.getName() + ".scope";

    @Override
    public boolean supportsContext(Observation.Context context) {
        return context instanceof RabbitMessageReceiverContext;
    }

    @Override
    public void onScopeOpened(RabbitMessageReceiverContext context) {
        MessageProperties properties = context.getCarrier().getMessageProperties();
        context.put(SCOPE_KEY, TraceContext.open(header(properties, TraceContext.TRACEPARENT_HEADER),
                header(properties, TraceContext.REQUEST_ID_HEADER)));
    }

    @Override
    public void onScopeClosed(RabbitMessageReceiverContext context) {
        TraceContext.Scope scope = context.get(SCOPE_KEY);
        if (scope != null) {
            scope.close();
        }
    }

    /** Only a text header is used; anything else (a number, a byte array) is treated as missing. */
    private static String header(MessageProperties properties, String name) {
        return properties.getHeader(name) instanceof String text ? text : null;
    }
}
