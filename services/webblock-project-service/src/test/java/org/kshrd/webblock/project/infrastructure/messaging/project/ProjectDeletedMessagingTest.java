package org.kshrd.webblock.project.infrastructure.messaging.project;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.project.handler.ProjectLifecycleService;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.rabbit.core.RabbitAdmin;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * project.project_deleted.v1 on real RabbitMQ: a test queue bound to project.events with the routing key
 * project_deleted.v1 receives exactly one envelope-only message for a delete, and none for a refused delete.
 */
@Tag("messaging")
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=4",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class ProjectDeletedMessagingTest {

    private static final String EXCHANGE = "project.events";
    private static final String ROUTING_KEY = "project_deleted.v1";
    private static final String QUEUE = "test.project_deleted.v1";
    private static final long RECEIVE_MILLIS = 10_000;
    private static final long SILENCE_MILLIS = 3_000;

    @Autowired
    ProjectLifecycleService lifecycle;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    RabbitAdmin admin;

    @Autowired
    RabbitTemplate rabbit;

    @Autowired
    JsonMapper json;

    @BeforeEach
    void queue() {
        admin.initialize();
        Queue queue = QueueBuilder.durable(QUEUE).quorum().build();
        admin.declareQueue(queue);
        admin.purgeQueue(QUEUE, false);
        Binding binding =
                BindingBuilder.bind(queue).to(new TopicExchange(EXCHANGE)).with(ROUTING_KEY);
        admin.declareBinding(binding);
    }

    @AfterEach
    void removeQueue() {
        admin.deleteQueue(QUEUE);
    }

    private UUID project(UUID owner) {
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Doomed", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        return projectId;
    }

    @Test
    @DisplayName("FR-38.6: a delete publishes project.project_deleted.v1, envelope only, snake_case, once")
    void fr38_6_aDeletePublishesTheEnvelopeOnly() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = project(owner);

        lifecycle.delete(projectId, owner);

        Message message = rabbit.receive(QUEUE, RECEIVE_MILLIS);
        assertThat(message).as("a project_deleted.v1 message within 10 s").isNotNull();
        MessageProperties properties = message.getMessageProperties();
        assertThat(properties.getReceivedExchange()).isEqualTo(EXCHANGE);
        assertThat(properties.getReceivedRoutingKey()).isEqualTo(ROUTING_KEY);
        assertThat(properties.getType()).isEqualTo("project.project_deleted.v1");
        assertThat(properties.getContentType()).isEqualTo("application/json");

        JsonNode body = json.readTree(message.getBody());
        Set<String> keys = new TreeSet<>();
        body.propertyNames().forEach(keys::add);
        assertThat(keys)
                .containsExactlyInAnyOrder(
                        "schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id");
        assertThat(body.get("schema_version").asInt()).isEqualTo(1);
        assertThat(body.get("message_type").asString()).isEqualTo("project.project_deleted.v1");
        assertThat(body.get("project_id").asString()).isEqualTo(projectId.toString());
        assertThat(body.get("correlation_id").asString()).isEqualTo(projectId.toString());
        assertThat(properties.getMessageId()).isEqualTo(body.get("message_id").asString());
        assertThat(UUID.fromString(body.get("message_id").asString()).version()).isEqualTo(7);
        assertThat(body.get("occurred_at").asString()).isNotBlank();

        assertThat(rabbit.receive(QUEUE, SILENCE_MILLIS))
                .as("exactly one message per delete")
                .isNull();
    }

    @Test
    @DisplayName("FR-38.2: a refused delete (a stranger, a repeat) publishes nothing")
    void fr38_2_aRefusedDeletePublishesNothing() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = project(owner);

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> lifecycle.delete(projectId, UUID.randomUUID()))
                .isInstanceOf(WebBlockException.class);

        assertThat(rabbit.receive(QUEUE, SILENCE_MILLIS))
                .as("no message for a stranger's delete")
                .isNull();
    }
}
