package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import io.namastack.outbox.OutboxPayloadSerializer;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.SerializationFeature;
import tools.jackson.databind.cfg.DateTimeFeature;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stores and reads outbox payloads with its own lenient mapper instead of the strict web mapper ({@code
 * JacksonConfig}). A payload written by another version of the code (a field added or removed) must still be sent:
 * failing to read it would retry forever and block the project's later events. The mapper is deliberately not a bean:
 * a second {@code JsonMapper} bean would switch Spring Boot's own mapper off.
 */
final class JsonOutboxPayloadSerializer implements OutboxPayloadSerializer {

    private final JsonMapper mapper = JsonMapper.builder()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(DateTimeFeature.WRITE_DATES_AS_TIMESTAMPS)
            .disable(SerializationFeature.INDENT_OUTPUT)
            .build();

    @Override
    public String serialize(Object payload) {
        return mapper.writeValueAsString(payload);
    }

    @Override
    public <T> T deserialize(String serialized, Class<T> type) {
        return mapper.readValue(serialized, type);
    }
}
