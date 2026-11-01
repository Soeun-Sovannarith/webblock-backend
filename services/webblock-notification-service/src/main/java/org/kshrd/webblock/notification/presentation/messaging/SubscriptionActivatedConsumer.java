package org.kshrd.webblock.notification.presentation.messaging;

import org.kshrd.webblock.notification.application.notification.handler.HandleSubscriptionActivatedHandler;
import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
public class SubscriptionActivatedConsumer {

    private static final Logger log = LoggerFactory.getLogger(SubscriptionActivatedConsumer.class);

    private final HandleSubscriptionActivatedHandler handler;
    private final EventPayloadValidator validator;

    public SubscriptionActivatedConsumer(HandleSubscriptionActivatedHandler handler, EventPayloadValidator validator) {
        this.handler = handler;
        this.validator = validator;
    }

    @RabbitListener(queues = RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE)
    public void consume(SubscriptionActivatedEvent event) {
        validator.validate(event);
        MDC.put("messageId", event.messageId().toString());
        try {
            log.info("Received subscription_activated. subscriptionId: [{}]", event.subscriptionId());
            handler.handle(event);
        } finally {
            MDC.remove("messageId");
        }
    }
}
