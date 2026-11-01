package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.kshrd.webblock.project.application.messaging.model.IntegrationEvent;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.SerializationFeature;
import tools.jackson.databind.cfg.DateTimeFeature;
import tools.jackson.databind.json.JsonMapper;

/** How an {@link IntegrationEvent} becomes an outbox key, an AMQP routing key and an AMQP message. */
final class IntegrationEvents {

    /**
     * Writes the body of an event as sent to other services: the record's own (snake_case) names, no type info. Built
     * here, not taken from Spring, so the strict web mapper ({@code JacksonConfig}) never changes what other services
     * read. Deliberately not a bean: a second {@code JsonMapper} bean would switch Spring Boot's own mapper off.
     */
    private static final JsonMapper EVENT_MAPPER = JsonMapper.builder()
            .disable(DateTimeFeature.WRITE_DATES_AS_TIMESTAMPS)
            .disable(SerializationFeature.INDENT_OUTPUT)
            .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS)
            .build();

    private static final Pattern MESSAGE_TYPE = Pattern.compile("project\\.([a-z_]+\\.v[1-9][0-9]*)");

    private IntegrationEvents() {}

    /**
     * The outbox key: the project ID, so that one project's events keep their order. Called while the publishing
     * transaction is open, so a malformed event fails that transaction instead of blocking the outbox later.
     */
    static String outboxKey(IntegrationEvent event) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            // Outside a transaction the outbox record would be committed on its own, whatever happens next.
            throw new IllegalStateException("An integration event must be published inside a transaction");
        }
        routingKey(event);
        if (event.messageId() == null || event.projectId() == null) {
            throw new IllegalArgumentException("An integration event needs a message_id and a project_id");
        }
        return event.projectId().toString();
    }

    /** {@code project.<event>.v<N>} is routed with {@code <event>.v<N>}. */
    static String routingKey(IntegrationEvent event) {
        String type = event.messageType();
        Matcher matcher = MESSAGE_TYPE.matcher(type == null ? "" : type);
        if (!matcher.matches()) {
            throw new IllegalArgumentException("message_type must be project.<event>.v<N>: " + type);
        }
        return matcher.group(1);
    }

    /** The record itself as the JSON body; {@code type} and {@code message_id} as AMQP properties; persistent. */
    static Message toMessage(IntegrationEvent event) {
        MessageProperties properties = new MessageProperties();
        properties.setContentType(MessageProperties.CONTENT_TYPE_JSON);
        properties.setContentEncoding(StandardCharsets.UTF_8.name());
        properties.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
        properties.setType(event.messageType());
        properties.setMessageId(event.messageId().toString());
        return new Message(EVENT_MAPPER.writeValueAsBytes(event), properties);
    }
}
