package org.kshrd.webblock.notification.presentation.messaging;

import org.kshrd.webblock.notification.application.notification.handler.HandlePublishCompletedHandler;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
public class PublishCompletedConsumer {

    private static final Logger log = LoggerFactory.getLogger(PublishCompletedConsumer.class);

    private final HandlePublishCompletedHandler handler;
    private final EventPayloadValidator validator;

    public PublishCompletedConsumer(HandlePublishCompletedHandler handler, EventPayloadValidator validator) {
        this.handler = handler;
        this.validator = validator;
    }

    @RabbitListener(queues = RabbitMQConfig.PUBLISH_COMPLETED_QUEUE)
    public void consume(PublishCompletedEvent event) {
        validator.validate(event);
        MDC.put("messageId", event.messageId().toString());
        try {
            log.info("Received publish_completed. projectId: [{}], status: [{}]", event.projectId(), event.status());
            handler.handle(event);
        } finally {
            MDC.remove("messageId");
        }
    }
}
