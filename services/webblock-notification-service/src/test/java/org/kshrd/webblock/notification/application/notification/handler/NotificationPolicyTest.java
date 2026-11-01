package org.kshrd.webblock.notification.application.notification.handler;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.kshrd.webblock.notification.TestWiring;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest.Alert;
import org.kshrd.webblock.notification.application.notification.request.HostingEntitlementEndedEvent;
import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.atLeast;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

/**
 * Who is told for each event, checked against the notification table in docs/specs/business-rules.md.
 * This looks at the point where a notification leaves our code (recipient role + Knock workflow key).
 * It cannot check the CHANNELS (web / email): they are set inside each Knock workflow, which no test here can see.
 * Workflow keys are the short names from TestProperties ("gen", "ok", "fail-owner", ...).
 */
class NotificationPolicyTest {

    private static Alert alert(String name, Map<String, String> extra) {
        var labels = new java.util.HashMap<>(extra);
        labels.put("alertname", name);
        return new Alert("firing", labels, "2026-10-30T08:00:00Z", "fp-1");
    }

    private static void alertRequest(TestWiring w, Alert alert) {
        w.alerts().handle(new AlertmanagerWebhookRequest(List.of(alert)));
    }

    private static Arguments row(String event, Consumer<TestWiring> action, String... expected) {
        return Arguments.of(event, action, List.of(expected));
    }

    static Stream<Arguments> matrix() {
        UUID projectId = UUID.randomUUID();
        String project = projectId.toString();
        return Stream.of(
                // business-rules.md: "Generation completes" -> owner only
                row("project.source_published.v1", w -> w.sourcePublished().handle(
                        new SourcePublishedEvent(UUID.randomUUID(), projectId, UUID.randomUUID())), "OWNER:gen"),
                // "Deployment succeeds" -> owner only
                row("provisioning.publish_completed.v1 LIVE", w -> w.publishCompleted().handle(
                        new PublishCompletedEvent(UUID.randomUUID(), projectId, UUID.randomUUID(), "LIVE", "k", null)), "OWNER:ok"),
                // "Deployment fails" -> admin, then owner
                row("provisioning.publish_completed.v1 FAILED", w -> w.publishCompleted().handle(
                        new PublishCompletedEvent(UUID.randomUUID(), projectId, UUID.randomUUID(), "FAILED", "k", "BUILD_FAILED")),
                        "ADMIN:fail-admin", "OWNER:fail-owner"),
                // no row in the spec for a rollback
                row("provisioning.publish_completed.v1 ROLLED_BACK", w -> w.publishCompleted().handle(
                        new PublishCompletedEvent(UUID.randomUUID(), projectId, UUID.randomUUID(), "ROLLED_BACK", "k", null))),
                // "A user's subscription expires" -> admin only
                row("payment.hosting_entitlement_ended.v1", w -> w.hostingEntitlementEnded().handle(
                        new HostingEntitlementEndedEvent(UUID.randomUUID(), UUID.randomUUID(), "k")), "ADMIN:expired"),
                // "A new subscription becomes Active" -> admin only
                row("payment.subscription_activated.v1", w -> w.subscriptionActivated().handle(
                        new SubscriptionActivatedEvent(UUID.randomUUID(), "k", UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID())),
                        "ADMIN:activated"),
                // "Monthly payment is due" -> owner only
                row("payment.payment_due.v1", w -> w.paymentDue().handle(
                        new PaymentDueEvent(UUID.randomUUID(), "k", UUID.fromString(TestWiring.OWNER_ID), UUID.randomUUID(), UUID.randomUUID(),
                                new BigDecimal("9.90"), Instant.parse("2026-11-30T00:00:00Z"))), "OWNER:due-owner"),
                // "Website generation gets stuck" -> admin only
                row("alert GenerationStuck", w -> alertRequest(w, alert("GenerationStuck", Map.of("project_id", project))),
                        "ADMIN:stuck-admin"),
                // "Website crashes" -> admin, then owner
                row("alert WebsiteDown", w -> alertRequest(w, alert("WebsiteDown", Map.of("project_id", project))),
                        "ADMIN:crash-admin", "OWNER:crash-owner"),
                // "Website exceeds its resource limits" -> admin, then owner
                row("alert WebsiteResourceLimitExceeded", w -> alertRequest(w, alert("WebsiteResourceLimitExceeded", Map.of("project_id", project))),
                        "ADMIN:limit-admin", "OWNER:limit-owner"),
                // "Platform service goes down" -> admin only
                row("alert PlatformServiceDown", w -> alertRequest(w, alert("PlatformServiceDown", Map.of("service", "user-service"))),
                        "ADMIN:down-admin"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("matrix")
    @DisplayName("tellsExactlyTheRecipientsTheBusinessRulesTableNames")
    void notifiesTheExpectedRecipientsAndNobodyElse(String event, Consumer<TestWiring> action, List<String> expected) {
        NotificationService sender = mock(NotificationService.class);

        action.accept(new TestWiring(sender));

        ArgumentCaptor<NotificationEventDto> sent = ArgumentCaptor.forClass(NotificationEventDto.class);
        if (expected.isEmpty()) {
            org.mockito.Mockito.verifyNoInteractions(sender);
            return;
        }
        verify(sender, atLeast(1)).processNotification(sent.capture(), anyString());
        List<String> actual = sent.getAllValues().stream().map(e -> e.recipientRole() + ":" + e.eventType()).toList();
        assertThat(actual).containsExactlyElementsOf(expected);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("matrix")
    @DisplayName("addressesOwnersAndAdminsByTheirOwnIdsNeverTheOtherGroup")
    void ownerAndAdminAreNeverMixedUp(String event, Consumer<TestWiring> action, List<String> expected) {
        NotificationService sender = mock(NotificationService.class);

        action.accept(new TestWiring(sender));

        ArgumentCaptor<NotificationEventDto> sent = ArgumentCaptor.forClass(NotificationEventDto.class);
        if (expected.isEmpty()) {
            return;
        }
        verify(sender, atLeast(1)).processNotification(sent.capture(), anyString());
        for (NotificationEventDto e : sent.getAllValues()) {
            if ("OWNER".equals(e.recipientRole())) {
                assertThat(e.recipientId()).isEqualTo(TestWiring.OWNER_ID);
            } else {
                assertThat(e.recipientRole()).isEqualTo("ADMIN");
                assertThat(e.recipientId()).isEqualTo(TestWiring.ADMIN_ID);
            }
        }
    }
}
