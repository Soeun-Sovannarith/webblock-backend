package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.json.JsonMapper;

/** Review fix I-1: stored payloads and event bodies do not depend on the web mapper. */
class OutboxJsonTest {

    private static final UUID MESSAGE_ID = UUID.fromString("01900000-0000-7000-8000-000000000001");
    private static final UUID PROJECT_ID = UUID.fromString("01900000-0000-7000-8000-000000000002");
    private static final ProbeHappened EVENT = new ProbeHappened(
            1, MESSAGE_ID, ProbeHappened.TYPE, Instant.parse("2026-10-14T01:02:03.456Z"), "corr-1", PROJECT_ID);

    private static final String EXPECTED_BODY = "{\"schema_version\":1,\"message_id\":\"" + MESSAGE_ID
            + "\",\"message_type\":\"project.probe_happened.v1\",\"occurred_at\":\"2026-10-14T01:02:03.456Z\","
            + "\"correlation_id\":\"corr-1\",\"project_id\":\"" + PROJECT_ID + "\"}";

    @Test
    void aStoredPayloadWithAnExtraFieldStillDeserializes() {
        var serializer = new JsonOutboxPayloadSerializer();
        String stored = EXPECTED_BODY.replace("}", ",\"added_by_a_later_version\":{\"x\":1}}");

        ProbeHappened read = serializer.deserialize(stored, ProbeHappened.class);

        assertThat(read).isEqualTo(EVENT);
    }

    @Test
    void aStoredPayloadWithAMissingFieldDeserializesWithANull() {
        var serializer = new JsonOutboxPayloadSerializer();
        String stored = EXPECTED_BODY.replace(",\"correlation_id\":\"corr-1\"", "");

        assertThat(serializer.deserialize(stored, ProbeHappened.class).correlationId())
                .isNull();
    }

    @Test
    void thePayloadRoundTrips() {
        var serializer = new JsonOutboxPayloadSerializer();
        assertThat(serializer.deserialize(serializer.serialize(EVENT), ProbeHappened.class))
                .isEqualTo(EVENT);
    }

    @Test
    void theEventBodyIsFixedByTheRecordNotByAnyOtherMapper() {
        // A web mapper reconfigured in every way a deployment might (strict reads, indentation, sorting, nulls).
        JsonMapper reconfiguredWeb = JsonMapper.builder()
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .enable(tools.jackson.databind.SerializationFeature.INDENT_OUTPUT)
                .enable(tools.jackson.databind.MapperFeature.SORT_PROPERTIES_ALPHABETICALLY)
                .build();
        assertThat(reconfiguredWeb.writeValueAsString(EVENT)).isNotEqualTo(EXPECTED_BODY);

        byte[] body = IntegrationEvents.toMessage(EVENT).getBody();

        assertThat(new String(body, StandardCharsets.UTF_8)).isEqualTo(EXPECTED_BODY);
    }
}
