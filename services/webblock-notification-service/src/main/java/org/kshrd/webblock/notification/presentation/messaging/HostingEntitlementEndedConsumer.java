package org.kshrd.webblock.notification.presentation.messaging;

import org.kshrd.webblock.notification.application.notification.handler.HandleHostingEntitlementEndedHandler;
import org.kshrd.webblock.notification.application.notification.request.HostingEntitlementEndedEvent;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
public class HostingEntitlementEndedConsumer {

    private static final Logger log = LoggerFactory.getLogger(HostingEntitlementEndedConsumer.class);

    private final HandleHostingEntitlementEndedHandler handler;
    private final EventPayloadValidator validator;

    public HostingEntitlementEndedConsumer(HandleHostingEntitlementEndedHandler handler, EventPayloadValidator validator) {
        this.handler = handler;
        this.validator = validator;
    }

    @RabbitListener(queues = RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE)
    public void consume(HostingEntitlementEndedEvent event) {
        validator.validate(event);
        MDC.put("messageId", event.messageId().toString());
        try {
            log.info("Received hosting_entitlement_ended. userId: [{}]", event.userId());
            handler.handle(event);
        } finally {
            MDC.remove("messageId");
        }
    }
}
