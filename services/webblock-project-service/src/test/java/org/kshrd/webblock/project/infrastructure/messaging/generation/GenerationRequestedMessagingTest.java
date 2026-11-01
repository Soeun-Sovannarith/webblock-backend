package org.kshrd.webblock.project.infrastructure.messaging.generation;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.handler.GenerationAdmissionService;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.support.GenerationRows;
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
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * project.generation_requested.v1 on real RabbitMQ: a test queue bound to project.events with the routing key
 * generation_requested.v1 receives what an admitted request publishes through the outbox, and nothing for a refused
 * one. The admission is called directly (outside a transaction, as the job does).
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
class GenerationRequestedMessagingTest {

    private static final String EXCHANGE = "project.events";
    private static final String ROUTING_KEY = "generation_requested.v1";
    private static final String QUEUE = "test.generation_requested.v1";
    private static final long RECEIVE_MILLIS = 10_000;
    private static final long SILENCE_MILLIS = 3_000;

    static Path standInFile;

    @DynamicPropertySource
    static void standIn(DynamicPropertyRegistry registry) throws Exception {
        standInFile = Files.createTempFile("generation-requested-refusals", ".json");
        standInFile.toFile().deleteOnExit();
        registry.add("webblock.payment.stand-in-file", standInFile::toString);
    }

    @Autowired
    GenerationAdmissionService admissions;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Clock clock;

    @Autowired
    RabbitAdmin admin;

    @Autowired
    RabbitTemplate rabbit;

    @Autowired
    JsonMapper json;

    GenerationRows rows;

    @BeforeEach
    void queue() throws Exception {
        Files.writeString(standInFile, "{}");
        rows = new GenerationRows(jdbc, clock);
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

    private record Captured(UUID projectId, UUID requestId) {}

    private Captured captured(UUID owner) {
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(new NewProject(
                projectId, owner, "Published", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        UUID requestId = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
        return new Captured(projectId, requestId);
    }

    @Test
    void anAdmittedRequestPublishesSnakeCaseWithItsCorrelationId() throws Exception {
        Captured c = captured(UUID.randomUUID());
        String hash = jdbc.queryForObject(
                "SELECT captured_input_hash FROM project.generation_requests WHERE generation_request_id = ?",
                String.class,
                c.requestId());

        admissions.admit(c.requestId());

        Message message = rabbit.receive(QUEUE, RECEIVE_MILLIS);
        assertThat(message).as("a generation_requested.v1 message within 10 s").isNotNull();
        MessageProperties properties = message.getMessageProperties();
        assertThat(properties.getReceivedExchange()).isEqualTo(EXCHANGE);
        assertThat(properties.getReceivedRoutingKey()).isEqualTo(ROUTING_KEY);
        assertThat(properties.getType()).isEqualTo("project.generation_requested.v1");
        assertThat(properties.getContentType()).isEqualTo("application/json");

        JsonNode body = json.readTree(message.getBody());
        Set<String> keys = new java.util.TreeSet<>();
        body.propertyNames().forEach(keys::add);
        assertThat(keys)
                .containsExactlyInAnyOrder(
                        "schema_version",
                        "message_id",
                        "message_type",
                        "occurred_at",
                        "correlation_id",
                        "project_id",
                        "generation_request_id",
                        "captured_input_hash");
        assertThat(body.get("schema_version").asInt()).isEqualTo(1);
        assertThat(body.get("message_type").asString()).isEqualTo("project.generation_requested.v1");
        assertThat(body.get("correlation_id").asString())
                .isEqualTo(c.requestId().toString());
        assertThat(body.get("generation_request_id").asString())
                .isEqualTo(c.requestId().toString());
        assertThat(body.get("project_id").asString()).isEqualTo(c.projectId().toString());
        assertThat(body.get("captured_input_hash").asString()).isEqualTo(hash).matches("[0-9a-f]{64}");
        assertThat(properties.getMessageId()).isEqualTo(body.get("message_id").asString());
        assertThat(UUID.fromString(body.get("message_id").asString()).version()).isEqualTo(7);
        assertThat(body.get("occurred_at").asString()).isNotBlank();

        assertThat(rabbit.receive(QUEUE, SILENCE_MILLIS))
                .as("exactly one message per request")
                .isNull();
    }

    @Test
    void aRefusedRequestPublishesNothing() throws Exception {
        UUID owner = UUID.randomUUID();
        Files.writeString(
                standInFile,
                json.writeValueAsString(Map.of(
                        "generationRefusals",
                        java.util.List.of(Map.of("clientUserId", owner.toString(), "reason", "NO_ACTIVE_PLAN")))));
        Captured c = captured(owner);

        admissions.admit(c.requestId());

        assertThat(jdbc.queryForObject(
                        "SELECT status FROM project.generation_requests WHERE generation_request_id = ?",
                        String.class,
                        c.requestId()))
                .isEqualTo("failed");
        assertThat(rabbit.receive(QUEUE, SILENCE_MILLIS))
                .as("no message for a refused request")
                .isNull();
    }
}
