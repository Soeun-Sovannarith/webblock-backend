package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.WebBlockApplication;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.AcknowledgeMode;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.Exchange;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitAdmin;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.listener.SimpleMessageListenerContainer;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.annotation.Import;
import org.springframework.core.env.Environment;
import org.springframework.scheduling.SchedulingAwareRunnable;
import org.springframework.scheduling.concurrent.SimpleAsyncTaskScheduler;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;
import org.springframework.scheduling.config.ScheduledTaskHolder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.util.ReflectionTestUtils;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.rabbitmq.RabbitMQContainer;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * Spike S1 (decision 0001, {@code .claude/docs/decisions/0001-outbox-with-modulith-and-namastack.md}): Spring Modulith
 * externalization through the Namastack outbox and its Rabbit module, on real RabbitMQ 4.3 and PostgreSQL 18. One test
 * per question (a)–(f), plus the review fixes I2 and I4.
 */
@Tag("messaging")
@ActiveProfiles("prototype")
@Testcontainers
@SpringBootTest(
        // MOCK, not NONE: the security chains of common/infrastructure/security need a servlet application
        // (HttpSecurity).
        webEnvironment = SpringBootTest.WebEnvironment.MOCK,
        properties = {
            "webblock.grpc.port=0",
            "namastack.outbox.polling.fixed.interval=200ms",
            "namastack.outbox.instance.heartbeat-interval=1s",
            "namastack.outbox.instance.rebalance-interval=1000",
            "webblock.catalog.migrations.enabled=false",
            "webblock.catalog.datasource.url=jdbc:postgresql://127.0.0.1:1/unused"
        })
@Import(OutboxSpikeBeans.class)
class OutboxSpikeTest {

    private static final Logger log = LoggerFactory.getLogger(OutboxSpikeTest.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String OBSERVER_QUEUE = "spike.observer";
    private static final String PROBE_ROUTING_KEY = "probe_happened.v1";

    @Container
    @ServiceConnection
    static final RabbitMQContainer rabbit = new RabbitMQContainer("rabbitmq:4.3.6-management-alpine");

    private static PostgresFixture.Database db;
    private static CachingConnectionFactory observerConnections;
    private static SimpleMessageListenerContainer observer;
    private static final List<Received> received = new CopyOnWriteArrayList<>();

    @Autowired
    ProbeService probes;

    @Autowired
    FlakyJobHandler jobs;

    @Autowired
    Environment environment;

    @Autowired
    ConfigurableApplicationContext context;

    record Received(Message message, Map<String, Object> body, Instant at) {
        String messageId() {
            return String.valueOf(body.get("message_id"));
        }

        String projectId() {
            return String.valueOf(body.get("project_id"));
        }
    }

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry registry) {
        db = newDatabase();
        registry.add("spring.datasource.url", db::url);
        registry.add("spring.datasource.username", () -> PostgresFixture.APP_LOGIN);
        registry.add("spring.datasource.password", () -> PostgresFixture.PASSWORD);
        registry.add("webblock.security.identity-mode", () -> "jwt");
        registry.add("spring.flyway.url", db::url);
        registry.add("spring.flyway.user", () -> PostgresFixture.MIGRATOR_LOGIN);
        registry.add("spring.flyway.password", () -> PostgresFixture.PASSWORD);
    }

    /**
     * A consumer outside every application context: one thread, raw AMQP messages, on a queue bound for the probe
     * events only (so other routing keys are unroutable). It declares the service's own topology first, identically.
     */
    @BeforeAll
    static void observe() {
        observerConnections = new CachingConnectionFactory(rabbit.getHost(), rabbit.getAmqpPort());
        observerConnections.setUsername(rabbit.getAdminUsername());
        observerConnections.setPassword(rabbit.getAdminPassword());
        RabbitAdmin admin = new RabbitAdmin(observerConnections);
        Declarables topology = new MessagingConfig().projectEventsTopology();
        topology.getDeclarablesByType(Exchange.class).forEach(admin::declareExchange);
        topology.getDeclarablesByType(Queue.class).forEach(admin::declareQueue);
        topology.getDeclarablesByType(Binding.class).forEach(admin::declareBinding);
        Queue queue = QueueBuilder.durable(OBSERVER_QUEUE).quorum().build();
        admin.declareQueue(queue);
        admin.declareBinding(new Binding(
                OBSERVER_QUEUE, Binding.DestinationType.QUEUE, MessagingConfig.EXCHANGE, PROBE_ROUTING_KEY, null));
        observer = new SimpleMessageListenerContainer(observerConnections);
        observer.setQueueNames(OBSERVER_QUEUE);
        observer.setConcurrentConsumers(1);
        observer.setAcknowledgeMode(AcknowledgeMode.AUTO);
        observer.setMessageListener(message -> received.add(new Received(
                message,
                JSON.readValue(message.getBody(), new TypeReference<Map<String, Object>>() {}),
                Instant.now())));
        observer.start();
    }

    @AfterAll
    static void stopObserving() {
        observer.stop();
        observerConnections.destroy();
    }

    /**
     * The application's broker connection and a channel are open before the pause (see {@link #warmUp()}), so the send
     * really reaches a channel while the broker is frozen, and the confirm can only come after it is unpaused.
     */
    @Test
    void marksSentOnlyAfterTheBrokerConfirms() throws Exception {
        warmUp();
        UUID project = UUID.randomUUID();
        ProbeHappened event = ProbeHappened.forProject(project);
        String id = event.messageId().toString();

        Instant beforeUnpause;
        pause();
        try {
            probes.publish(event);
            await().atMost(Duration.ofSeconds(3))
                    .pollInterval(Duration.ofMillis(50))
                    .untilAsserted(
                            () -> assertThat(OutboxSpikeBeans.PUBLISH_ATTEMPTS).containsKey(id));
            Thread.sleep(5_000);
            Map<String, Object> during = outboxRow(db, project.toString());
            log.info("S1 (a) sent to a channel during the pause; outbox row 5 s later: {}", during);
            assertThat(during.get("status")).isEqualTo("NEW");
            assertThat(during.get("completed_at")).isNull();
            assertThat(during.get("failure_count")).isEqualTo(0);
            assertThat(receivedFor(project)).isEmpty();
            beforeUnpause = Instant.now();
        } finally {
            unpause();
        }

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            assertThat(receivedFor(project)).hasSize(1);
            assertThat(outboxRow(db, project.toString()).get("status")).isEqualTo("COMPLETED");
        });
        Map<String, Object> after = outboxRow(db, project.toString());
        assertThat(((java.sql.Timestamp) after.get("completed_at")).toInstant())
                .as("completed only once the confirm could arrive, after the unpause")
                .isAfter(beforeUnpause);
        assertThat(after.get("failure_count")).isEqualTo(0);
        assertThat(OutboxSpikeBeans.PUBLISH_ATTEMPTS.get(id))
                .as("one send, confirmed late")
                .hasSize(1);
        Thread.sleep(2_000);
        assertThat(receivedFor(project)).hasSize(1);
        log.info(
                "S1 (a) after unpause: received {} time(s); outbox row {}",
                receivedFor(project).size(),
                after);
    }

    /** (a), continued: a confirm that never comes within the timeout is a failed attempt, retried; never "sent". */
    @Test
    void aConfirmTimeoutIsAFailedAttemptAndIsRetried() throws Exception {
        warmUp();
        UUID project = UUID.randomUUID();
        ProbeHappened event = ProbeHappened.forProject(project);
        String id = event.messageId().toString();

        pause();
        try {
            probes.publish(event);
            await().atMost(Duration.ofSeconds(3))
                    .pollInterval(Duration.ofMillis(50))
                    .untilAsserted(
                            () -> assertThat(OutboxSpikeBeans.PUBLISH_ATTEMPTS).containsKey(id));
            await().atMost(Duration.ofSeconds(25))
                    .pollInterval(Duration.ofMillis(200))
                    .untilAsserted(() -> assertThat(
                                    (Integer) outboxRow(db, project.toString()).get("failure_count"))
                            .isGreaterThanOrEqualTo(1));
            Map<String, Object> during = outboxRow(db, project.toString());
            log.info("S1 (a) outbox row after a confirm timeout during the pause: {}", during);
            assertThat(during.get("status")).isEqualTo("NEW");
            assertThat(during.get("completed_at")).isNull();
            assertThat((String) during.get("failure_reason")).contains("publisher confirm");
            assertThat(receivedFor(project)).isEmpty();
        } finally {
            unpause();
        }

        await().atMost(Duration.ofSeconds(60))
                .untilAsserted(
                        () -> assertThat(outboxRow(db, project.toString()).get("status"))
                                .isEqualTo("COMPLETED"));
        Thread.sleep(3_000);
        assertThat(receivedFor(project)).isNotEmpty();
        assertThat(receivedFor(project))
                .allSatisfy(r -> assertThat(r.messageId()).isEqualTo(id));
        log.info(
                "S1 (a) after unpause: sent {} time(s), received {} time(s) (a resend after a lost confirm is a duplicate"
                        + " with the same message_id); outbox row {}",
                OutboxSpikeBeans.PUBLISH_ATTEMPTS.get(id).size(),
                receivedFor(project).size(),
                outboxRow(db, project.toString()));
    }

    @Test
    void messagesCarryOurTypeIdAndPlainSnakeCaseBody() {
        UUID project = UUID.randomUUID();
        ProbeHappened event = ProbeHappened.forProject(project);

        probes.publish(event);

        await().atMost(Duration.ofSeconds(15))
                .untilAsserted(() -> assertThat(receivedFor(project)).hasSize(1));
        Received got = receivedFor(project).getFirst();
        var properties = got.message().getMessageProperties();
        log.info(
                "S1 (b) received: exchange={} routingKey={} type={} messageId={} contentType={} deliveryMode={}"
                        + " headers={} body={}",
                properties.getReceivedExchange(),
                properties.getReceivedRoutingKey(),
                properties.getType(),
                properties.getMessageId(),
                properties.getContentType(),
                properties.getReceivedDeliveryMode(),
                properties.getHeaders(),
                new String(got.message().getBody(), java.nio.charset.StandardCharsets.UTF_8));

        assertThat(properties.getReceivedExchange()).isEqualTo("project.events");
        assertThat(properties.getReceivedRoutingKey()).isEqualTo("probe_happened.v1");
        assertThat(properties.getType()).isEqualTo("project.probe_happened.v1");
        assertThat(properties.getMessageId()).isEqualTo(event.messageId().toString());
        assertThat(properties.getContentType()).isEqualTo("application/json");
        assertThat(properties.getHeaders()).doesNotContainKey("__TypeId__");
        assertThat(got.body())
                .containsOnlyKeys(
                        "schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id")
                .containsEntry("schema_version", 1)
                .containsEntry("message_id", event.messageId().toString())
                .containsEntry("message_type", "project.probe_happened.v1")
                .containsEntry("correlation_id", event.correlationId())
                .containsEntry("project_id", project.toString());
        assertThat(Instant.parse((String) got.body().get("occurred_at"))).isEqualTo(event.occurredAt());
    }

    @Test
    void outboxTablesComeFromFlywayOnly() throws Exception {
        assertThat(environment.getProperty("namastack.outbox.jdbc.schema-initialization.enabled"))
                .isEqualTo("false");
        assertThat(environment.getProperty("spring.modulith.events.jdbc.schema-initialization.enabled"))
                .isNull();
        List<List<Object>> tables = rows(
                db,
                "SELECT t.table_schema, t.table_name, p.tableowner FROM information_schema.tables t "
                        + "JOIN pg_tables p ON p.schemaname = t.table_schema AND p.tablename = t.table_name "
                        + "WHERE t.table_name LIKE 'outbox%' OR t.table_name LIKE '%event_publication%' ORDER BY 2");
        log.info("S1 (c) outbox tables: {}", tables);
        assertThat(tables)
                .containsExactly(
                        List.of("project_messaging", "outbox_instance", "project_migrator"),
                        List.of("project_messaging", "outbox_partition", "project_migrator"),
                        List.of("project_messaging", "outbox_record", "project_migrator"));
        List<List<Object>> history = rows(
                db,
                "SELECT version, description, success FROM project_ops.flyway_schema_history ORDER BY installed_rank");
        log.info("S1 (c) Flyway history: {}", history);
        assertThat(history).contains(List.of("4", "outbox", true));
        await().atMost(Duration.ofSeconds(10))
                .untilAsserted(() -> assertThat(rows(db, "SELECT count(*) FROM project_messaging.outbox_partition"))
                        .containsExactly(List.of(256L)));
        // The application login's search_path ("$user", public) does not contain project_messaging: the rows below
        // exist
        // only because Namastack names project_messaging.<table> in every statement
        // (namastack.outbox.jdbc.schema-name).
        assertThat(rows(db, "SELECT setting FROM pg_settings WHERE name = 'search_path'"))
                .containsExactly(List.of("\"$user\", public"));
        assertThat(rows(db, "SELECT count(*) FROM pg_db_role_setting"))
                .as("no role or database overrides search_path")
                .containsExactly(List.of(0L));
        UUID project = UUID.randomUUID();
        probes.publish(ProbeHappened.forProject(project));
        await().atMost(Duration.ofSeconds(15))
                .untilAsserted(
                        () -> assertThat(outboxRow(db, project.toString()).get("status"))
                                .isEqualTo("COMPLETED"));
        assertThat(
                        rows(
                                db,
                                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'project_ops' ORDER BY 1"))
                .containsExactly(List.of("flyway_schema_history"));
    }

    /** I4: polling and rebalance both run on MessagingConfig's one-thread scheduler, not on Namastack's default. */
    @Test
    void pollingAndRebalanceRunOnOurSingleThreadScheduler() throws Exception {
        Object ours = context.getBean("outboxDefaultScheduler");
        assertThat(ours)
                .isInstanceOfSatisfying(
                        ThreadPoolTaskScheduler.class,
                        s -> assertThat(s.getPoolSize()).isEqualTo(1));
        Object processing = context.getBean(Class.forName("io.namastack.outbox.OutboxProcessingScheduler"));
        assertThat(ReflectionTestUtils.getField(processing, "taskScheduler"))
                .as("the scheduler Namastack's polling cycle runs on")
                .isSameAs(ours);
        // Spring wraps each scheduled method; the wrapper keeps the method's name (toString) and its scheduler
        // qualifier.
        List<SchedulingAwareRunnable> rebalance = context.getBeansOfType(ScheduledTaskHolder.class).values().stream()
                .flatMap(holder -> holder.getScheduledTasks().stream())
                .map(task -> task.getTask().getRunnable())
                .filter(r -> r.toString().equals("io.namastack.outbox.partition.PartitionCoordinator.rebalance"))
                .map(SchedulingAwareRunnable.class::cast)
                .toList();
        assertThat(rebalance)
                .as("the partition rebalance is scheduled once, on the scheduler bean named outboxDefaultScheduler")
                .singleElement()
                .satisfies(r -> assertThat(r.getQualifier()).isEqualTo("outboxDefaultScheduler"));
        // Spring Boot's own "taskScheduler" (application @Scheduled work) is not Namastack's and is left alone.
        assertThat(java.util.Arrays.stream(context.getBeanFactory().getBeanNamesForType(SimpleAsyncTaskScheduler.class))
                        .filter(name -> name.startsWith("outbox")))
                .as("of Namastack's virtual-thread schedulers only the heartbeat remains; it runs no outbox work")
                .containsExactly("outboxHeartbeatScheduler");
    }

    /** I2: an event no queue is bound for is confirmed and kept in project.events.unrouted, not dropped. */
    @Test
    void unroutableEventsLandInTheUnroutedQueue() throws Exception {
        UUID project = UUID.randomUUID();
        ProbeHappened event = new ProbeHappened(
                1,
                com.fasterxml.uuid.Generators.timeBasedEpochGenerator().generate(),
                "project.nobody_listens.v1",
                Instant.now(),
                "corr-unrouted",
                project);

        // The broker's topology is removed, then declared again by the application alone: what follows proves that
        // the application declares project.events with its alternate exchange and queue.
        RabbitAdmin observerAdmin = new RabbitAdmin(observerConnections);
        observerAdmin.deleteExchange(MessagingConfig.EXCHANGE);
        observerAdmin.deleteExchange(MessagingConfig.UNROUTED);
        observerAdmin.deleteQueue(MessagingConfig.UNROUTED);
        context.getBean(RabbitAdmin.class).initialize();
        observerAdmin.declareBinding(new Binding(
                OBSERVER_QUEUE, Binding.DestinationType.QUEUE, MessagingConfig.EXCHANGE, PROBE_ROUTING_KEY, null));

        probes.publish(event);

        await().atMost(Duration.ofSeconds(15))
                .untilAsserted(
                        () -> assertThat(outboxRow(db, project.toString()).get("status"))
                                .isEqualTo("COMPLETED"));
        RabbitTemplate reader = new RabbitTemplate(observerConnections);
        Message kept = null;
        for (int i = 0; i < 20 && kept == null; i++) {
            Message m = reader.receive(MessagingConfig.UNROUTED, 1_000);
            if (m != null
                    && event.messageId()
                            .toString()
                            .equals(m.getMessageProperties().getMessageId())) {
                kept = m;
            }
        }
        assertThat(kept)
                .as("the unroutable event is in %s", MessagingConfig.UNROUTED)
                .isNotNull();
        log.info(
                "S1 unrouted: type={} routingKey={} exchange={}",
                kept.getMessageProperties().getType(),
                kept.getMessageProperties().getReceivedRoutingKey(),
                kept.getMessageProperties().getReceivedExchange());
        assertThat(kept.getMessageProperties().getType()).isEqualTo("project.nobody_listens.v1");
        assertThat(kept.getMessageProperties().getReceivedRoutingKey()).isEqualTo("nobody_listens.v1");
        assertThat(receivedFor(project)).isEmpty();
    }

    @Test
    void twoInstancesNeverPublishTheSameRecordAndKeepKeyOrder() throws Exception {
        PostgresFixture.Database shared = newDatabase();
        List<ConfigurableApplicationContext> instances = new ArrayList<>();
        try {
            // Instance two joins while instance one is already sending, so partitions change hands under load.
            instances.add(startInstance(shared, "one"));
            assertThat(instances.getFirst().getBean("outboxDefaultScheduler"))
                    .as("polling and rebalance share one platform thread (MessagingConfig)")
                    .isInstanceOfSatisfying(
                            ThreadPoolTaskScheduler.class,
                            s -> assertThat(s.getPoolSize()).isEqualTo(1));
            List<ProbeService> services = new CopyOnWriteArrayList<>();
            services.add(instances.getFirst().getBean(ProbeService.class));

            int keys = 10;
            int perKey = 20;
            Map<UUID, List<String>> published = new LinkedHashMap<>();
            ExecutorService publishers = Executors.newFixedThreadPool(keys);
            // The second half of each key waits until instance two owns partitions, so both instances send whatever
            // the start-up time of instance two is.
            CountDownLatch twoOwnsPartitions = new CountDownLatch(1);
            List<Future<?>> done = new ArrayList<>();
            for (int k = 0; k < keys; k++) {
                UUID project = UUID.randomUUID();
                List<String> order = new CopyOnWriteArrayList<>();
                published.put(project, order);
                done.add(publishers.submit(() -> {
                    for (int i = 0; i < perKey; i++) {
                        if (i == perKey / 2) {
                            assertThat(twoOwnsPartitions.await(2, TimeUnit.MINUTES))
                                    .as("instance two owns partitions")
                                    .isTrue();
                        }
                        ProbeHappened event = ProbeHappened.forProject(project);
                        services.get(i % services.size()).publish(event);
                        order.add(event.messageId().toString());
                        Thread.sleep(150);
                    }
                    return null;
                }));
            }
            Thread.sleep(1_000);
            instances.add(startInstance(shared, "two"));
            services.add(instances.get(1).getBean(ProbeService.class));
            await().atMost(Duration.ofMinutes(2))
                    .pollInterval(Duration.ofMillis(200))
                    .untilAsserted(() -> assertThat(rows(
                                    shared,
                                    "SELECT count(DISTINCT instance_id) FROM project_messaging.outbox_partition"))
                            .containsExactly(List.of(2L)));
            twoOwnsPartitions.countDown();
            for (Future<?> f : done) {
                f.get(2, TimeUnit.MINUTES);
            }
            publishers.shutdown();

            await().atMost(Duration.ofMinutes(2))
                    .pollInterval(Duration.ofMillis(500))
                    .untilAsserted(() -> assertThat(published.keySet().stream()
                                    .mapToInt(p -> receivedFor(p).size())
                                    .sum())
                            .isGreaterThanOrEqualTo(keys * perKey));
            Thread.sleep(3_000);

            Map<String, Long> perInstance = new LinkedHashMap<>();
            for (var entry : published.entrySet()) {
                List<String> arrived = receivedFor(entry.getKey()).stream()
                        .map(Received::messageId)
                        .toList();
                assertThat(arrived).doesNotHaveDuplicates();
                assertThat(arrived).containsExactlyElementsOf(entry.getValue());
                for (String id : entry.getValue()) {
                    List<String> senders = OutboxSpikeBeans.PUBLISH_ATTEMPTS.get(id);
                    assertThat(senders).as("send attempts of %s", id).hasSize(1);
                    perInstance.merge(senders.getFirst(), 1L, Long::sum);
                }
            }
            List<List<Object>> partitions = rows(
                    shared,
                    "SELECT instance_id, count(*) FROM project_messaging.outbox_partition GROUP BY instance_id ORDER BY 1");
            List<List<Object>> statuses = rows(
                    shared, "SELECT status, count(*) FROM project_messaging.outbox_record GROUP BY status ORDER BY 1");
            log.info(
                    "S1 (d) {} events on {} keys: sent by instance {}; partitions {}; outbox statuses {}",
                    keys * perKey,
                    keys,
                    perInstance,
                    partitions,
                    statuses);
            assertThat(statuses).containsExactly(List.of("COMPLETED", (long) keys * perKey));
            assertThat(perInstance).containsOnlyKeys("one", "two");
        } finally {
            for (ConfigurableApplicationContext instance : instances) {
                instance.close();
            }
        }
    }

    @Test
    void localJobsRetryWithBackoff() throws Exception {
        UUID jobId = UUID.randomUUID();

        probes.schedule(new ProbeJob(jobId, 2));

        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(() -> assertThat(outboxRow(db, jobId.toString()).get("status"))
                        .isEqualTo("COMPLETED"));
        List<Instant> attempts = jobs.attemptsOf(jobId);
        Map<String, Object> row = outboxRow(db, jobId.toString());
        log.info("S1 (e) attempts {}; outbox row {}", attempts, row);
        assertThat(attempts).hasSize(3);
        for (int i = 1; i < attempts.size(); i++) {
            Duration gap = Duration.between(attempts.get(i - 1), attempts.get(i));
            assertThat(gap).isGreaterThanOrEqualTo(FlakyJobHandler.BACKOFF);
            assertThat(gap).isLessThan(FlakyJobHandler.BACKOFF.plusMillis(1_500));
        }
        assertThat(row.get("failure_count")).isEqualTo(2);
        assertThat(rows(
                        db,
                        "SELECT handler_id FROM project_messaging.outbox_record WHERE record_key = ?",
                        jobId.toString()))
                .as("one record, for the job handler only: a local job never goes to the broker")
                .singleElement()
                .satisfies(
                        handler -> assertThat((String) handler.getFirst()).startsWith(FlakyJobHandler.class.getName()));
    }

    @Test
    void rolledBackTransactionsPublishNothing() throws Exception {
        UUID project = UUID.randomUUID();
        ProbeHappened event = ProbeHappened.forProject(project);

        assertThatThrownBy(() -> probes.publishThenFail(event)).isInstanceOf(IllegalStateException.class);
        Thread.sleep(3_000);

        assertThat(rows(db, "SELECT id FROM project_messaging.outbox_record WHERE record_key = ?", project.toString()))
                .isEmpty();
        assertThat(rows(
                        db,
                        "SELECT message_id FROM project_messaging.processed_messages WHERE message_id = ?",
                        event.messageId()))
                .isEmpty();
        assertThat(receivedFor(project)).isEmpty();
        assertThat(OutboxSpikeBeans.PUBLISH_ATTEMPTS)
                .doesNotContainKey(event.messageId().toString());
        log.info("S1 (f) rolled back: no outbox record, no business row, no message for {}", event.messageId());
    }

    /**
     * Publishes one event and waits for it, so the application's broker connection and a cached channel exist. Each
     * pause test calls it, so none depends on the order the tests run in.
     */
    private void warmUp() {
        UUID project = UUID.randomUUID();
        probes.publish(ProbeHappened.forProject(project));
        await().atMost(Duration.ofSeconds(15))
                .untilAsserted(() -> assertThat(receivedFor(project)).hasSize(1));
    }

    /** A second application instance in this JVM; command-line arguments, so they win over application.yml. */
    private static ConfigurableApplicationContext startInstance(PostgresFixture.Database database, String name) {
        return new SpringApplicationBuilder(WebBlockApplication.class, OutboxSpikeBeans.class)
                // SERVLET on random ports: the security chains of common/infrastructure/security need a servlet
                // application.
                .web(WebApplicationType.SERVLET)
                .profiles("prototype")
                .run(
                        "--webblock.security.identity-mode=jwt",
                        "--server.port=0",
                        "--management.server.port=0",
                        "--webblock.grpc.port=0",
                        "--spike.instance=" + name,
                        "--spike.publish-delay-ms=200",
                        "--spring.datasource.url=" + database.url(),
                        "--spring.datasource.username=" + PostgresFixture.APP_LOGIN,
                        "--spring.datasource.password=" + PostgresFixture.PASSWORD,
                        "--spring.flyway.url=" + database.url(),
                        "--spring.flyway.user=" + PostgresFixture.MIGRATOR_LOGIN,
                        "--spring.flyway.password=" + PostgresFixture.PASSWORD,
                        "--spring.rabbitmq.host=" + rabbit.getHost(),
                        "--spring.rabbitmq.port=" + rabbit.getAmqpPort(),
                        "--spring.rabbitmq.username=" + rabbit.getAdminUsername(),
                        "--spring.rabbitmq.password=" + rabbit.getAdminPassword(),
                        "--namastack.outbox.polling.fixed.interval=200ms",
                        "--namastack.outbox.instance.heartbeat-interval=1s",
                        "--namastack.outbox.instance.rebalance-interval=1000",
                        "--webblock.catalog.migrations.enabled=false",
                        "--webblock.catalog.datasource.url=jdbc:postgresql://127.0.0.1:1/unused");
    }

    private static List<Received> receivedFor(UUID project) {
        return received.stream()
                .filter(r -> r.projectId().equals(project.toString()))
                .toList();
    }

    private static void pause() {
        rabbit.getDockerClient().pauseContainerCmd(rabbit.getContainerId()).exec();
    }

    private static void unpause() {
        rabbit.getDockerClient().unpauseContainerCmd(rabbit.getContainerId()).exec();
    }

    private static PostgresFixture.Database newDatabase() {
        try {
            return PostgresFixture.freshDatabase(false);
        } catch (Exception e) {
            throw new IllegalStateException("could not create the spike database", e);
        }
    }

    private static Map<String, Object> outboxRow(PostgresFixture.Database database, String key) throws SQLException {
        try (Connection c = database.asSuperuser();
                PreparedStatement ps =
                        c.prepareStatement("SELECT status, completed_at, failure_count, failure_reason, handler_id "
                                + "FROM project_messaging.outbox_record WHERE record_key = ?")) {
            ps.setString(1, key);
            try (ResultSet rs = ps.executeQuery()) {
                Map<String, Object> row = new LinkedHashMap<>();
                if (rs.next()) {
                    for (int i = 1; i <= rs.getMetaData().getColumnCount(); i++) {
                        row.put(rs.getMetaData().getColumnName(i), rs.getObject(i));
                    }
                }
                return row;
            }
        }
    }

    private static List<List<Object>> rows(PostgresFixture.Database database, String sql, Object... params)
            throws SQLException {
        try (Connection c = database.asSuperuser();
                PreparedStatement ps = c.prepareStatement(sql)) {
            for (int i = 0; i < params.length; i++) {
                ps.setObject(i + 1, params[i]);
            }
            try (ResultSet rs = ps.executeQuery()) {
                List<List<Object>> result = new ArrayList<>();
                while (rs.next()) {
                    List<Object> row = new ArrayList<>();
                    for (int i = 1; i <= rs.getMetaData().getColumnCount(); i++) {
                        row.add(rs.getObject(i));
                    }
                    result.add(row);
                }
                return result;
            }
        }
    }
}
