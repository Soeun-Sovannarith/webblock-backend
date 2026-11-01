package org.kshrd.webblock.project.application.messaging.model;

import java.util.UUID;

/**
 * An event other services consume (communication note, section 1). Implementations are snake_case records carrying the
 * envelope fields ({@code schema_version}, {@code message_id}, {@code message_type}, {@code occurred_at}, {@code
 * correlation_id}, {@code project_id}) plus their own fields; the record is the whole message body.
 *
 * <p>Publishing one with {@code ApplicationEventPublisher.publishEvent} inside a transaction stores it in the outbox; it
 * is sent after the commit and never if the transaction rolls back.
 */
public interface IntegrationEvent {

    /** The envelope's {@code message_id} (UUIDv7); also the AMQP {@code message_id}. */
    UUID messageId();

    /** The envelope's {@code message_type}, {@code project.<event>.v<N>}; also the AMQP {@code type}. */
    String messageType();

    /** The envelope's {@code project_id}; events of one project are sent in the order they were published. */
    UUID projectId();
}
