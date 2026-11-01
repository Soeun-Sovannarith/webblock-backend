package org.kshrd.webblock.notification;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.application.notification.handler.HandlePaymentDueHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandlePublishCompletedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandleSubscriptionActivatedHandler;
import org.kshrd.webblock.notification.application.notification.handler.NotificationService;
import org.kshrd.webblock.notification.application.notification.handler.OwnerNotifier;
import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.kshrd.webblock.notification.common.domain.PermanentFailure;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/**
 * The service as Kubernetes runs it: profile "k8s" (the "local-stub" default is replaced). No user-service or
 * project-service client exists yet, so owner notifications must fail closed, while admin-only notifications keep working.
 * Knock is a mock (NotificationService) and the key is a dummy: nothing here calls Knock.
 */
@SpringBootTest(properties = {
        "knock.api-key=dummy-key-not-a-real-credential",
        "webblock.notification.alertmanager.webhook-token=dummy-token",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "spring.rabbitmq.listener.simple.auto-startup=false",
        "webblock.notification.admins[0].id=admin-1",
        "webblock.notification.admins[0].name=Test Admin",
        "webblock.notification.admins[0].email=admin-1@example.test"})
@ActiveProfiles("k8s")
class K8sProfileStartupTest {

    @MockitoBean
    private NotificationService notificationService;

    @Autowired
    private OwnerNotifier ownerNotifier;
    @Autowired
    private HandleSubscriptionActivatedHandler subscriptionActivated;
    @Autowired
    private HandlePublishCompletedHandler publishCompleted;
    @Autowired
    private HandlePaymentDueHandler paymentDue;

    @Test
    @DisplayName("startsWithTheK8sProfileEvenThoughNoRealOwnerOrContactClientExistsYet")
    void startsUnderK8s() {
        assertThat(ownerNotifier).isNotNull();
    }

    @Test
    @DisplayName("failsClosedForOwnerNotificationsWithoutInventingARecipient")
    void ownerLookupFailsClosed() {
        assertThatThrownBy(() -> ownerNotifier.notifyOwner("t.v1", "k", UUID.randomUUID(), UUID.randomUUID(), "wf", Map.of()))
                .isInstanceOf(PermanentFailure.class);
        assertThatThrownBy(() -> ownerNotifier.notifyUser("t.v1", "k", UUID.randomUUID(), "user-1", "wf", Map.of()))
                .isInstanceOf(PermanentFailure.class);
        verify(notificationService, never()).processNotification(any(), anyString());
    }

    @Test
    @DisplayName("stillNotifiesAdminsForEventsThatNeedNoOwnerLookup")
    void adminOnlyEventStillWorks() {
        subscriptionActivated.handle(new SubscriptionActivatedEvent(UUID.randomUUID(), "key-1", UUID.randomUUID(),
                UUID.randomUUID(), UUID.randomUUID()));

        ArgumentCaptor<NotificationEventDto> sent = ArgumentCaptor.forClass(NotificationEventDto.class);
        verify(notificationService).processNotification(sent.capture(), anyString());
        assertThat(sent.getValue().recipientRole()).isEqualTo("ADMIN");
        assertThat(sent.getValue().recipientId()).isEqualTo("admin-1");
    }

    @Test
    @DisplayName("notifiesAdminsOfAFailedDeployEvenWhenTheOwnerCannotBeFoundYet")
    void failedDeployAdminFirstThenOwnerFailsClosed() {
        var event = new PublishCompletedEvent(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), "FAILED", "key-1", "BUILD_FAILED");

        assertThatThrownBy(() -> publishCompleted.handle(event)).isInstanceOf(PermanentFailure.class);

        ArgumentCaptor<NotificationEventDto> sent = ArgumentCaptor.forClass(NotificationEventDto.class);
        verify(notificationService).processNotification(sent.capture(), anyString());
        assertThat(sent.getValue().recipientRole()).isEqualTo("ADMIN");
    }

    @Test
    @DisplayName("failsClosedForPaymentDueBecauseTheOwnerContactIsUnavailable")
    void paymentDueFailsClosed() {
        var event = new PaymentDueEvent(UUID.randomUUID(), "key-1", UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(),
                new BigDecimal("9.90"), Instant.parse("2026-11-30T00:00:00Z"));

        assertThatThrownBy(() -> paymentDue.handle(event)).isInstanceOf(PermanentFailure.class);
        verify(notificationService, never()).processNotification(any(), anyString());
    }
}
