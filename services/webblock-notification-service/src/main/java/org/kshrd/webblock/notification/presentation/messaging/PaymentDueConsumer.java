package org.kshrd.webblock.notification.presentation.messaging;

import org.kshrd.webblock.notification.application.notification.handler.HandlePaymentDueHandler;
import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
public class PaymentDueConsumer {

    private static final Logger log = LoggerFactory.getLogger(PaymentDueConsumer.class);

    private final HandlePaymentDueHandler handler;
    private final EventPayloadValidator validator;

    public PaymentDueConsumer(HandlePaymentDueHandler handler, EventPayloadValidator validator) {
        this.handler = handler;
        this.validator = validator;
    }

    @RabbitListener(queues = RabbitMQConfig.PAYMENT_DUE_QUEUE)
    public void consume(PaymentDueEvent event) {
        validator.validate(event);
        MDC.put("messageId", event.messageId().toString());
        try {
            log.info("Received payment_due. subscriptionId: [{}]", event.subscriptionId());
            handler.handle(event);
        } finally {
            MDC.remove("messageId");
        }
    }
}
