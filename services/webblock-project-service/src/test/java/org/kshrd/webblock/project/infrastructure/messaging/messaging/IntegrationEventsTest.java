package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.transaction.support.TransactionSynchronizationManager;

class IntegrationEventsTest {

    @BeforeEach
    void inATransaction() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
    }

    @AfterEach
    void outOfTheTransaction() {
        TransactionSynchronizationManager.setActualTransactionActive(false);
    }

    @Test
    void refusesToPublishOutsideATransaction() {
        TransactionSynchronizationManager.setActualTransactionActive(false);
        assertThatThrownBy(() -> IntegrationEvents.outboxKey(withType(ProbeHappened.TYPE)))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("transaction");
    }

    private static ProbeHappened withType(String type) {
        return new ProbeHappened(1, UUID.randomUUID(), type, Instant.now(), "c", UUID.randomUUID());
    }

    @Test
    void routesProjectEventsByTheirEventNameAndVersion() {
        assertThat(IntegrationEvents.routingKey(withType("project.source_published.v1")))
                .isEqualTo("source_published.v1");
        assertThat(IntegrationEvents.routingKey(withType("project.project_deleted.v12")))
                .isEqualTo("project_deleted.v12");
    }

    @Test
    void refusesMessageTypesOutsideTheConvention() {
        for (String type : new String[] {
            "ai_codegen.generation_completed.v1",
            "project.SourcePublished.v1",
            "project.source_published",
            "project.x.v0",
            ""
        }) {
            assertThatThrownBy(() -> IntegrationEvents.outboxKey(withType(type)))
                    .as(type)
                    .isInstanceOf(IllegalArgumentException.class);
        }
        assertThatThrownBy(() -> IntegrationEvents.routingKey(withType(null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void refusesAnEventWithoutItsIdentifiers() {
        var noProject = new ProbeHappened(1, UUID.randomUUID(), ProbeHappened.TYPE, Instant.now(), "c", null);
        var noMessageId = new ProbeHappened(1, null, ProbeHappened.TYPE, Instant.now(), "c", UUID.randomUUID());
        assertThatThrownBy(() -> IntegrationEvents.outboxKey(noProject)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> IntegrationEvents.outboxKey(noMessageId)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void theOutboxKeyIsTheProjectId() {
        ProbeHappened event = withType(ProbeHappened.TYPE);
        assertThat(IntegrationEvents.outboxKey(event))
                .isEqualTo(event.projectId().toString());
    }

    @Test
    void buildsAPersistentJsonMessageWithOurTypeAndId() {
        ProbeHappened event = withType(ProbeHappened.TYPE);
        var message = IntegrationEvents.toMessage(event);
        var properties = message.getMessageProperties();
        assertThat(properties.getType()).isEqualTo(ProbeHappened.TYPE);
        assertThat(properties.getMessageId()).isEqualTo(event.messageId().toString());
        assertThat(properties.getContentType()).isEqualTo("application/json");
        assertThat(properties.getDeliveryMode()).isEqualTo(MessageDeliveryMode.PERSISTENT);
        assertThat(properties.getHeaders()).isEmpty();
        assertThat(new String(message.getBody(), java.nio.charset.StandardCharsets.UTF_8))
                .contains("\"message_id\":\"" + event.messageId() + "\"")
                .doesNotContain("messageId");
    }
}
