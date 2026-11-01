package org.kshrd.webblock.notification.infrastructure.messaging;

import app.knock.api.client.KnockClient;
import app.knock.api.core.JsonValue;
import app.knock.api.core.http.Headers;
import app.knock.api.errors.InternalServerException;
import app.knock.api.errors.UnauthorizedException;
import app.knock.api.models.users.User;
import app.knock.api.models.users.UserUpdateParams;
import app.knock.api.models.workflows.WorkflowTriggerParams;
import app.knock.api.models.workflows.WorkflowTriggerResponse;
import app.knock.api.services.blocking.UserService;
import app.knock.api.services.blocking.WorkflowService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.TestMethodOrder;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.kshrd.webblock.notification.infrastructure.observability.NotificationMetrics;
import org.slf4j.MDC;
import org.springframework.amqp.core.AmqpAdmin;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageBuilderSupport;
import org.springframework.amqp.core.QueueInformation;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.listener.RabbitListenerEndpointRegistry;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.IntFunction;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * Real broker test against the LOCAL Docker RabbitMQ (localhost:5672, management on localhost:15672).
 * Runs the real application: real RabbitMQConfig topology, real listeners, real retry advice, real consumers, handlers and
 * KnockNotificationService. Only the Knock SDK client is a mock (scripted per scenario): Knock is never contacted.
 *
 * Safety:
 * - Opt-in only: set NOTIFICATION_BROKER_IT=local. A normal "./gradlew test" skips this class and touches no broker.
 * - The host is fixed to "localhost" below; RABBITMQ_* environment variables are ignored. The deployed broker is never contacted.
 * - It works in its own throw-away vhost "notification-it-xxxxxxxx", created and deleted through the management API.
 *   Queues in the default vhost "/" (and any service instance consuming them) are not touched.
 */
@SpringBootTest(properties = {
        "eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "knock.api-key=dummy-key-not-a-real-credential",
        "webblock.notification.admins[0].id=admin-it-1",
        "webblock.notification.admins[0].name=Test Admin",
        "webblock.notification.admins[0].email=admin-it-1@example.test"})
@EnabledIfEnvironmentVariable(named = "NOTIFICATION_BROKER_IT", matches = "local")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@DirtiesContext
class LocalBrokerIntegrationTest {

    private static final String HOST = "localhost";
    private static final int AMQP_PORT = 5672;
    private static final String MANAGEMENT = "http://localhost:15672";
    private static final String USER = "guest";   // RabbitMQ's default development account, the same default as application.yml
    private static final String PASSWORD = "guest";
    private static final String VHOST_PREFIX = "notification-it-";
    private static final String VHOST = VHOST_PREFIX + UUID.randomUUID().toString().substring(0, 8);

    /** The queue and exchange used for the scenarios: admin-only event, so no owner lookup is needed. */
    private static final String QUEUE = RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE;
    private static final String DLQ = QUEUE + ".dlq";

    /** Configured in application.yml: max-retries 4 = 5 tries in total; delays 1s, 2s, 4s, 8s between them. */
    private static final int TRIES = 5;

    private static final ObjectMapper JSON = new ObjectMapper();

    @DynamicPropertySource
    static void localBroker(DynamicPropertyRegistry registry) {
        LocalBrokerSupport.createVhost(VHOST);
        // The exchanges belong to the sending services and this service no longer declares them: create them as the senders would
        LocalBrokerSupport.createSenderExchanges(VHOST);
        registry.add("spring.rabbitmq.host", () -> HOST);
        registry.add("spring.rabbitmq.port", () -> AMQP_PORT);
        registry.add("spring.rabbitmq.username", () -> USER);
        registry.add("spring.rabbitmq.password", () -> PASSWORD);
        registry.add("spring.rabbitmq.virtual-host", () -> VHOST);
    }

    @MockitoBean
    private KnockClient knockClient;

    @Autowired
    private RabbitTemplate rabbitTemplate;
    @Autowired
    private AmqpAdmin amqpAdmin;
    @Autowired
    private RabbitListenerEndpointRegistry listenerRegistry;

    @Autowired
    private MeterRegistry meterRegistry;

    private final AtomicInteger identifyCalls = new AtomicInteger();
    private final AtomicInteger triggerCalls = new AtomicInteger();
    private final AtomicInteger published = new AtomicInteger();
    private final List<String> idempotencyKeys = new CopyOnWriteArrayList<>();
    /** What the logging context held on the listener thread at each Knock trigger (null when empty). */
    private final List<String> traceIdsSeen = new CopyOnWriteArrayList<>();
    private final List<String> requestIdsSeen = new CopyOnWriteArrayList<>();
    /** Which exception (if any) the Knock mock throws on the n-th trigger call (1-based). */
    private volatile IntFunction<RuntimeException> knockTriggerFailure = n -> null;

    @BeforeEach
    void scriptKnock() {
        identifyCalls.set(0);
        triggerCalls.set(0);
        idempotencyKeys.clear();
        traceIdsSeen.clear();
        requestIdsSeen.clear();
        knockTriggerFailure = n -> null;
        UserService userService = mock(UserService.class);
        WorkflowService workflowService = mock(WorkflowService.class);
        when(knockClient.users()).thenReturn(userService);
        when(knockClient.workflows()).thenReturn(workflowService);
        when(userService.update(any(UserUpdateParams.class))).thenAnswer(invocation -> {
            identifyCalls.incrementAndGet();
            return mock(User.class);
        });
        when(workflowService.trigger(any(WorkflowTriggerParams.class))).thenAnswer(invocation -> {
            WorkflowTriggerParams params = invocation.getArgument(0);
            idempotencyKeys.add(params._additionalHeaders().values("Idempotency-Key").get(0));
            traceIdsSeen.add(MDC.get("trace_id"));
            requestIdsSeen.add(MDC.get("request_id"));
            RuntimeException failure = knockTriggerFailure.apply(triggerCalls.incrementAndGet());
            if (failure != null) {
                throw failure;
            }
            return mock(WorkflowTriggerResponse.class);
        });
    }

    @AfterAll
    void stopTheListenersThenDeleteTheTestVhost() {
        // Stop only the consumers, so they do not keep reconnecting to a vhost that is about to go. Spring closes the
        // context itself afterwards (@DirtiesContext); closing it here as well makes Spring's own shutdown fail.
        listenerRegistry.stop();
        LocalBrokerSupport.deleteVhost(VHOST);   // refuses any vhost that these tests did not create
    }

    // ---- helpers -------------------------------------------------------------------------------------------

    private static String management(String method, String path, String body) {
        return LocalBrokerSupport.management(method, path, body);
    }

    private int depth(String queue) {
        QueueInformation info = amqpAdmin.getQueueInfo(queue);
        return info == null ? -1 : (int) info.getMessageCount();
    }

    private int consumers(String queue) {
        QueueInformation info = amqpAdmin.getQueueInfo(queue);
        return info == null ? -1 : info.getConsumerCount();
    }

    private static String event(UUID messageId, String eventKey, boolean complete) {
        String planId = complete ? ",\"plan_id\":\"" + UUID.randomUUID() + "\"" : "";
        return "{\"message_id\":\"" + messageId + "\",\"event_key\":\"" + eventKey + "\",\"subscription_id\":\"" + UUID.randomUUID()
                + "\",\"client_user_id\":\"" + UUID.randomUUID() + "\"" + planId + "}";
    }

    private void publish(String body) {
        publish(body, Map.of());
    }

    private void publish(String body, Map<String, Object> headers) {
        MessageBuilderSupport<Message> builder = MessageBuilder.withBody(body.getBytes(StandardCharsets.UTF_8)).setContentType("application/json");
        headers.forEach(builder::setHeader);
        rabbitTemplate.send("payment.events", "subscription_activated.v1", builder.build());
        published.incrementAndGet();
    }

    private double counter(String name, String... tags) {
        return meterRegistry.get(name).tags(tags).counter().count();
    }

    private long timerCount(String step, String outcome, String statusClass) {
        return meterRegistry.get(NotificationMetrics.KNOCK_CALL)
                .tags("step", step, "outcome", outcome, "status_class", statusClass).timer().count();
    }

    private static void log(String line) {
        System.out.println("[broker-it] " + line);
    }

    // ---- scenarios -----------------------------------------------------------------------------------------

    @Test
    @Order(1)
    @DisplayName("connectsToTheLocalBrokerAndDeclaresEveryQueueWithItsDlqAndOneConsumer")
    void connectsAndDeclaresTheTopology() {
        assertThat(VHOST).startsWith(VHOST_PREFIX).isNotEqualTo("/");
        Boolean channelOpen = rabbitTemplate.execute(channel -> channel.isOpen());
        assertThat(channelOpen).isTrue();

        List<String> queues = List.of(RabbitMQConfig.SOURCE_PUBLISHED_QUEUE, RabbitMQConfig.PUBLISH_COMPLETED_QUEUE,
                RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE, RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE,
                RabbitMQConfig.PAYMENT_DUE_QUEUE);
        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            for (String queue : queues) {
                assertThat(consumers(queue)).as("consumers on " + queue).isEqualTo(1);
                assertThat(consumers(queue + ".dlq")).as("consumers on " + queue + ".dlq").isZero();
                assertThat(depth(queue + ".dlq")).as(queue + ".dlq exists and is empty").isZero();
            }
        });
        log("connected to " + HOST + ":" + AMQP_PORT + " vhost " + VHOST + "; 5 queues, each with 1 consumer, and 5 empty DLQs");
    }

    @Test
    @Order(2)
    @DisplayName("consumesAGoodMessageAndCallsKnockOnce")
    void consumesAGoodMessage() {
        publish(event(UUID.randomUUID(), "good-1", true));

        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(triggerCalls.get()).isEqualTo(1));
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> assertThat(depth(QUEUE)).isZero());
        assertThat(identifyCalls.get()).isEqualTo(1);
        assertThat(depth(DLQ)).isZero();
        log("good message consumed: 1 identify + 1 trigger, queue empty, DLQ empty");
    }

    @Test
    @Order(3)
    @DisplayName("deadLettersAPermanentKnockFailureAfterOneTryWithoutRetries")
    void permanentFailureGoesToTheDlqAfterOneTry() throws Exception {
        knockTriggerFailure = n -> UnauthorizedException.builder().headers(Headers.builder().build()).body(JsonValue.from(Map.of())).build();
        int before = depth(DLQ);
        long started = System.nanoTime();

        publish(event(UUID.randomUUID(), "permanent-1", true));

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(depth(DLQ)).isEqualTo(before + 1));
        long seconds = Duration.ofNanos(System.nanoTime() - started).toSeconds();
        assertThat(triggerCalls.get()).as("Knock tries before the message was dead-lettered").isEqualTo(1);
        Thread.sleep(3000);   // a retry would show up now (first retry delay is 1s)
        assertThat(triggerCalls.get()).as("Knock tries 3s later").isEqualTo(1);
        assertThat(depth(QUEUE)).isZero();
        log("permanent (401): dead-lettered after " + triggerCalls.get() + " try, in about " + seconds + "s (a retried message would take about 15s)");
    }

    @Test
    @Order(4)
    @DisplayName("retriesATemporaryKnockFailureForTheConfiguredTriesThenDeadLettersIt")
    void temporaryFailureIsRetriedThenDeadLettered() {
        knockTriggerFailure = n -> InternalServerException.builder().statusCode(503).headers(Headers.builder().build()).body(JsonValue.from(Map.of())).build();
        int before = depth(DLQ);
        long started = System.nanoTime();

        publish(event(UUID.randomUUID(), "temporary-1", true));

        await().atMost(Duration.ofSeconds(60)).untilAsserted(() -> assertThat(depth(DLQ)).isEqualTo(before + 1));
        Duration took = Duration.ofNanos(System.nanoTime() - started);
        assertThat(triggerCalls.get()).as("Knock tries before dead-lettering").isEqualTo(TRIES);
        assertThat(took).as("1s + 2s + 4s + 8s of backoff between the tries").isGreaterThanOrEqualTo(Duration.ofSeconds(14));
        assertThat(idempotencyKeys).as("every retry reuses the same Idempotency-Key").hasSize(TRIES).containsOnly(idempotencyKeys.get(0));
        assertThat(depth(QUEUE)).isZero();
        log("temporary (503): " + triggerCalls.get() + " tries over " + took.toSeconds() + "s, then dead-lettered; same Idempotency-Key on all tries");
    }

    @Test
    @Order(5)
    @DisplayName("deliversTheMessageWhenATemporaryFailureClearsBeforeTheLimit")
    void temporaryFailureThatClearsIsDelivered() {
        knockTriggerFailure = n -> n <= 2
                ? InternalServerException.builder().statusCode(503).headers(Headers.builder().build()).body(JsonValue.from(Map.of())).build()
                : null;
        int before = depth(DLQ);

        publish(event(UUID.randomUUID(), "recovers-1", true));

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(triggerCalls.get()).isEqualTo(3));
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> assertThat(depth(QUEUE)).isZero());
        assertThat(depth(DLQ)).as("nothing new in the DLQ").isEqualTo(before);
        log("recovering failure: delivered on try 3, nothing dead-lettered");
    }

    @Test
    @Order(6)
    @DisplayName("deadLettersAnInvalidEventAndAnUnreadableBodyWithoutCallingKnock")
    void invalidMessagesGoStraightToTheDlq() {
        int before = depth(DLQ);
        long started = System.nanoTime();

        publish(event(UUID.randomUUID(), "invalid-1", false));   // plan_id missing
        publish("this is not json");

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(depth(DLQ)).isEqualTo(before + 2));
        long seconds = Duration.ofNanos(System.nanoTime() - started).toSeconds();
        assertThat(identifyCalls.get()).isZero();
        assertThat(triggerCalls.get()).isZero();
        assertThat(depth(QUEUE)).isZero();
        log("invalid event + unreadable body: both dead-lettered in about " + seconds + "s, Knock never called");
    }

    @Test
    @Order(7)
    @DisplayName("keepsDeadLettersAvailableWithoutExpiryPurgeOrConsumer")
    void deadLettersStayAvailable() throws Exception {
        int expected = 1 + 1 + 2;   // permanent + exhausted retries + invalid + unreadable
        assertThat(depth(DLQ)).isEqualTo(expected);

        Thread.sleep(5000);
        assertThat(depth(DLQ)).as("nothing removed while waiting").isEqualTo(expected);
        assertThat(consumers(DLQ)).as("no consumer drains the DLQ").isZero();

        JsonNode queue = JSON.readTree(management("GET", "/api/queues/" + VHOST + "/" + DLQ, null));
        JsonNode arguments = queue.path("arguments");
        for (String expiry : List.of("x-message-ttl", "x-max-length", "x-max-length-bytes", "x-expires", "x-overflow")) {
            assertThat(arguments.has(expiry)).as("DLQ argument " + expiry).isFalse();
        }
        assertThat(queue.path("durable").asBoolean()).isTrue();
        assertThat(JSON.readTree(management("GET", "/api/policies/" + VHOST, null))).as("no policy (TTL/max-length) on the vhost").isEmpty();

        // read one message and put it back, to prove the content is still there
        String body = rabbitTemplate.execute(channel -> {
            var response = channel.basicGet(DLQ, false);
            String text = new String(response.getBody(), StandardCharsets.UTF_8);
            channel.basicNack(response.getEnvelope().getDeliveryTag(), false, true);
            return text;
        });
        assertThat(body).contains("event_key");
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> assertThat(depth(DLQ)).isEqualTo(expected));
        log("DLQ holds " + expected + " messages after 5s, no consumer, no TTL/max-length/expires, message body readable and put back");
    }

    @Test
    @Order(8)
    @DisplayName("continuesTheSendersTraceInTheLogContextOnEveryRetryAndRemovesItForTheNextMessage")
    void traceContextCoversRetriesAndDoesNotLeak() {
        String traceId = "4bf92f3577b34da6a3ce929d0e0e4736";
        knockTriggerFailure = n -> n <= 2
                ? InternalServerException.builder().statusCode(503).headers(Headers.builder().build()).body(JsonValue.from(Map.of())).build()
                : null;

        publish(event(UUID.randomUUID(), "trace-1", true), Map.of(
                "traceparent", "00-" + traceId + "-00f067aa0ba902b7-01",
                "x-request-id", "req-from-sender-1"));

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(triggerCalls.get()).isEqualTo(3));
        assertThat(traceIdsSeen).as("trace id on all 3 tries, retries included").containsExactly(traceId, traceId, traceId);
        assertThat(requestIdsSeen).containsExactly("req-from-sender-1", "req-from-sender-1", "req-from-sender-1");

        // the next message has no headers: nothing of the previous one may be left on the listener thread
        triggerCalls.set(0);
        traceIdsSeen.clear();
        requestIdsSeen.clear();
        knockTriggerFailure = n -> null;
        publish(event(UUID.randomUUID(), "trace-2", true));

        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(triggerCalls.get()).isEqualTo(1));
        assertThat(traceIdsSeen).containsExactly((String) null);
        assertThat(requestIdsSeen.get(0)).isNotNull().isNotEqualTo("req-from-sender-1");
        log("trace context from the message headers was in the log context on all 3 tries, and absent for the next message");
    }

    @Test
    @Order(9)
    @DisplayName("reportsExactlyWhatHappenedInTheMetrics")
    void metricsMatchWhatHappened() {
        String queue = QUEUE;
        String workflow = "subscription-activated-admin";

        // 8 messages were published to the queue in this class; all of them reached the listener
        assertThat(published.get()).isEqualTo(8);
        assertThat(counter(NotificationMetrics.EVENTS_RECEIVED, "queue", queue)).isEqualTo(8);
        // permanent 401 + retries used up + invalid event + unreadable body
        assertThat(counter(NotificationMetrics.EVENTS_DEAD_LETTERED, "queue", queue)).isEqualTo(4);
        assertThat(counter(NotificationMetrics.DELIVERY, "workflow", workflow, "outcome", "success")).isEqualTo(4);
        assertThat(counter(NotificationMetrics.DELIVERY, "workflow", workflow, "outcome", "temporary_failure")).isEqualTo(9);
        assertThat(counter(NotificationMetrics.DELIVERY, "workflow", workflow, "outcome", "permanent_failure")).isEqualTo(1);
        // the other queues saw nothing
        assertThat(counter(NotificationMetrics.EVENTS_RECEIVED, "queue", RabbitMQConfig.PAYMENT_DUE_QUEUE)).isZero();

        assertThat(timerCount("trigger", "permanent_failure", "4xx")).isEqualTo(1);
        assertThat(timerCount("trigger", "temporary_failure", "5xx")).isEqualTo(9);
        assertThat(timerCount("trigger", "success", "none")).isEqualTo(4);
        assertThat(timerCount("identify", "success", "none")).as("identify runs on every try").isEqualTo(14);
        assertThat(meterRegistry.find("spring.rabbit.listener").timer())
                .as("Spring AMQP's own listener timer exists because observation is enabled").isNotNull();
        log("metrics: received=8, dead-lettered=4, delivery success/temporary/permanent=4/9/1, Knock call timers match");
    }
}
