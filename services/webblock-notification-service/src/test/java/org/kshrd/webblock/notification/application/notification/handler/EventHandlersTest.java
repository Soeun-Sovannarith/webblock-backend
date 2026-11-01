package org.kshrd.webblock.notification.application.notification.handler;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.TestProperties;
import org.kshrd.webblock.notification.application.notification.request.HostingEntitlementEndedEvent;
import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;

/** The event handlers that only map one event to one notifier call. */
@ExtendWith(MockitoExtension.class)
class EventHandlersTest {

    @Mock
    private OwnerNotifier ownerNotifier;

    @Mock
    private AdminNotifier adminNotifier;

    private final UUID messageId = UUID.randomUUID();
    private final UUID projectId = UUID.randomUUID();

    @Test
    @DisplayName("sourcePublishedUsesProjectAndGenerationRequestAsTheKey")
    void sourcePublishedKeyIsProjectAndGeneration() {
        UUID generationRequestId = UUID.randomUUID();

        new HandleSourcePublishedHandler(ownerNotifier, TestProperties.PROPERTIES)
                .handle(new SourcePublishedEvent(messageId, projectId, generationRequestId));

        // The same fact re-sent with a new message_id keeps this key
        verify(ownerNotifier).notifyOwner(eq("project.source_published.v1"), eq(projectId + ":" + generationRequestId),
                eq(messageId), eq(projectId), eq("gen"), eq(Map.of()));
    }

    @Test
    @DisplayName("sourcePublishedFallsBackToTheMessageIdWithoutAGenerationRequest")
    void sourcePublishedKeyFallsBackToMessageId() {
        new HandleSourcePublishedHandler(ownerNotifier, TestProperties.PROPERTIES)
                .handle(new SourcePublishedEvent(messageId, projectId, null));

        verify(ownerNotifier).notifyOwner(eq("project.source_published.v1"), eq(messageId.toString()),
                eq(messageId), eq(projectId), eq("gen"), eq(Map.of()));
    }

    @Test
    @DisplayName("hostingEntitlementEndedTellsTheAdminsTheSubscriptionExpired")
    void hostingEntitlementEndedTellsAdmins() {
        UUID userId = UUID.randomUUID();

        new HandleHostingEntitlementEndedHandler(adminNotifier, TestProperties.PROPERTIES)
                .handle(new HostingEntitlementEndedEvent(messageId, userId, "key-1"));

        verify(adminNotifier).notifyAdmins(eq("payment.hosting_entitlement_ended.v1"), eq("key-1"), eq(messageId),
                eq("expired"), eq(Map.of("userId", userId.toString())));
    }

    @Test
    @DisplayName("subscriptionActivatedTellsTheAdmins")
    void subscriptionActivatedTellsAdmins() {
        UUID subscriptionId = UUID.randomUUID();

        new HandleSubscriptionActivatedHandler(adminNotifier, TestProperties.PROPERTIES)
                .handle(new SubscriptionActivatedEvent(messageId, "key-1", subscriptionId, UUID.randomUUID(), UUID.randomUUID()));

        verify(adminNotifier).notifyAdmins(eq("payment.subscription_activated.v1"), eq("key-1"), eq(messageId),
                eq("activated"), anyMap());
    }
}
