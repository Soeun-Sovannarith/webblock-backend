package org.kshrd.webblock.notification.presentation.messaging;

import org.kshrd.webblock.notification.application.notification.handler.HandleSourcePublishedHandler;
import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
public class SourcePublishedConsumer {

    private static final Logger log = LoggerFactory.getLogger(SourcePublishedConsumer.class);

    private final HandleSourcePublishedHandler handler;
    private final EventPayloadValidator validator;

    public SourcePublishedConsumer(HandleSourcePublishedHandler handler, EventPayloadValidator validator) {
        this.handler = handler;
        this.validator = validator;
    }

    @RabbitListener(queues = RabbitMQConfig.SOURCE_PUBLISHED_QUEUE)
    public void consume(SourcePublishedEvent event) {
        validator.validate(event);
        MDC.put("messageId", event.messageId().toString());
        try {
            log.info("Received source_published. projectId: [{}]", event.projectId());
            handler.handle(event);
        } finally {
            MDC.remove("messageId");
        }
    }
}
