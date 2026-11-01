package org.kshrd.webblock.notification.application.notification.handler;

import app.knock.api.client.KnockClient;
import app.knock.api.models.users.User;
import app.knock.api.models.users.UserUpdateParams;
import app.knock.api.models.workflows.WorkflowTriggerParams;
import app.knock.api.models.workflows.WorkflowTriggerResponse;
import app.knock.api.services.blocking.UserService;
import app.knock.api.services.blocking.WorkflowService;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.kshrd.webblock.notification.TestWiring;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest.Alert;
import org.kshrd.webblock.notification.application.notification.request.HostingEntitlementEndedEvent;
import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.kshrd.webblock.notification.infrastructure.client.knock.KnockSdkDeliveryClient;
import org.kshrd.webblock.notification.infrastructure.observability.NotificationMetrics;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeast;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The same event delivered twice (RabbitMQ delivers at least once) must reach Knock with the same Idempotency-Key,
 * so that Knock can drop the repeat. Real handlers, notifiers and KnockNotificationService; only the Knock SDK client is a mock.
 * IMPORTANT: this proves our key is stable. It does NOT prove Knock drops the repeat, or for how long it remembers a key:
 * that is Knock's behaviour and is not tested here (no call to Knock is made).
 */
class DuplicateDeliveryTest {

    private static final String HEADER = "Idempotency-Key";

    private WorkflowService workflowService;
    private TestWiring wiring;

    @BeforeEach
    void setUp() {
        KnockClient knockClient = mock(KnockClient.class);
        UserService userService = mock(UserService.class);
        workflowService = mock(WorkflowService.class);
        when(knockClient.users()).thenReturn(userService);
        when(knockClient.workflows()).thenReturn(workflowService);
        when(userService.update(any(UserUpdateParams.class))).thenReturn(mock(User.class));
        when(workflowService.trigger(any(WorkflowTriggerParams.class))).thenReturn(mock(WorkflowTriggerResponse.class));
        NotificationMetrics metrics = new NotificationMetrics(new SimpleMeterRegistry());
        wiring = new TestWiring(new KnockNotificationService(new KnockSdkDeliveryClient(knockClient, metrics), metrics));
    }

    private List<String> sentKeys() {
        ArgumentCaptor<WorkflowTriggerParams> sent = ArgumentCaptor.forClass(WorkflowTriggerParams.class);
        verify(workflowService, atLeast(1)).trigger(sent.capture());
        return sent.getAllValues().stream().map(p -> p._additionalHeaders().values(HEADER).get(0)).toList();
    }

    private static Alert alert(String name, String fingerprint, String startsAt, Map<String, String> extra) {
        var labels = new java.util.HashMap<>(extra);
        labels.put("alertname", name);
        return new Alert("firing", labels, startsAt, fingerprint);
    }

    /** Each case runs the event with two different message_ids (a re-send gets a new one) and expects the same keys. */
    static Stream<Arguments> events() {
        UUID projectId = UUID.randomUUID();
        UUID generationRequestId = UUID.randomUUID();
        UUID releaseId = UUID.randomUUID();
        UUID userId = UUID.fromString(TestWiring.OWNER_ID);
        UUID subscriptionId = UUID.randomUUID();
        UUID planId = UUID.randomUUID();
        return Stream.of(
                Arguments.of("source_published", (BiConsumer<TestWiring, UUID>) (w, messageId) ->
                        w.sourcePublished().handle(new SourcePublishedEvent(messageId, projectId, generationRequestId))),
                Arguments.of("publish_completed FAILED (admin + owner)", (BiConsumer<TestWiring, UUID>) (w, messageId) ->
                        w.publishCompleted().handle(new PublishCompletedEvent(messageId, projectId, releaseId, "FAILED", "key-1", "BUILD_FAILED"))),
                Arguments.of("hosting_entitlement_ended", (BiConsumer<TestWiring, UUID>) (w, messageId) ->
                        w.hostingEntitlementEnded().handle(new HostingEntitlementEndedEvent(messageId, userId, "key-1"))),
                Arguments.of("subscription_activated", (BiConsumer<TestWiring, UUID>) (w, messageId) ->
                        w.subscriptionActivated().handle(new SubscriptionActivatedEvent(messageId, "key-1", subscriptionId, userId, planId))),
                Arguments.of("payment_due", (BiConsumer<TestWiring, UUID>) (w, messageId) ->
                        w.paymentDue().handle(new PaymentDueEvent(messageId, "key-1", userId, subscriptionId, planId,
                                new BigDecimal("9.90"), Instant.parse("2026-11-30T00:00:00Z")))),
                // Alertmanager re-sends the same alert on repeat_interval; the id comes from alert name + fingerprint + start time
                Arguments.of("alertmanager WebsiteDown", (BiConsumer<TestWiring, UUID>) (w, ignored) ->
                        w.alerts().handle(new AlertmanagerWebhookRequest(List.of(
                                alert("WebsiteDown", "fp-1", "2026-10-30T08:00:00Z", Map.of("project_id", projectId.toString()))))))
        );
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("events")
    @DisplayName("sendsTheSameIdempotencyKeyWhenTheSameEventIsDeliveredTwice")
    void sameEventSameKey(String event, BiConsumer<TestWiring, UUID> deliver) {
        deliver.accept(wiring, UUID.randomUUID());
        deliver.accept(wiring, UUID.randomUUID());

        List<String> keys = sentKeys();
        assertThat(keys).isNotEmpty();
        assertThat(keys.size() % 2).as("two deliveries send the same number of notifications").isZero();
        int perDelivery = keys.size() / 2;
        assertThat(keys.subList(0, perDelivery)).containsExactlyElementsOf(keys.subList(perDelivery, keys.size()));
        assertThat(keys).allSatisfy(k -> assertThat(k).isNotBlank().doesNotContain("@"));
    }

    @Test
    @DisplayName("sendsADifferentKeyForADifferentEventOrADifferentIncident")
    void differentEventDifferentKey() {
        UUID projectId = UUID.randomUUID();
        wiring.publishCompleted().handle(new PublishCompletedEvent(UUID.randomUUID(), projectId, UUID.randomUUID(), "LIVE", "key-1", null));
        wiring.publishCompleted().handle(new PublishCompletedEvent(UUID.randomUUID(), projectId, UUID.randomUUID(), "LIVE", "key-2", null));
        // the same alert starting again later is a new incident
        wiring.alerts().handle(new AlertmanagerWebhookRequest(List.of(alert("WebsiteDown", "fp-1", "2026-10-30T08:00:00Z", Map.of("project_id", projectId.toString())))));
        wiring.alerts().handle(new AlertmanagerWebhookRequest(List.of(alert("WebsiteDown", "fp-1", "2026-10-31T08:00:00Z", Map.of("project_id", projectId.toString())))));

        assertThat(sentKeys()).doesNotHaveDuplicates();
    }
}
