package org.kshrd.webblock.notification.application.notification.handler;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.TestProperties;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class HandlePublishCompletedHandlerTest {

    static final NotificationProperties PROPERTIES = TestProperties.PROPERTIES;

    @Mock
    private OwnerNotifier ownerNotifier;

    @Mock
    private AdminNotifier adminNotifier;

    private final UUID messageId = UUID.randomUUID();
    private final UUID projectId = UUID.randomUUID();
    private final UUID releaseId = UUID.randomUUID();

    private HandlePublishCompletedHandler handler() {
        return new HandlePublishCompletedHandler(ownerNotifier, adminNotifier, PROPERTIES);
    }

    private PublishCompletedEvent event(String status, String failureCode) {
        return new PublishCompletedEvent(messageId, projectId, releaseId, status, "key-1", failureCode);
    }

    @Test
    @DisplayName("tellsOnlyTheOwnerWhenTheDeploymentIsLive")
    void tellsOnlyTheOwnerWhenLive() {
        handler().handle(event("LIVE", null));

        verify(ownerNotifier).notifyOwner(eq("provisioning.publish_completed.v1"), eq("key-1"), eq(messageId),
                eq(projectId), eq("ok"), anyMap());
        verify(adminNotifier, never()).notifyAdmins(any(), any(), any(), any(), anyMap());
    }

    @Test
    @DisplayName("tellsTheAdminsFirstThenTheOwnerWhenTheDeploymentFails")
    void tellsAdminsThenOwnerWhenFailed() {
        handler().handle(event("FAILED", "BUILD_FAILED"));

        // Admins first: if the owner lookup keeps failing the message ends in the DLQ, but the admins already know
        InOrder order = inOrder(adminNotifier, ownerNotifier);
        order.verify(adminNotifier).notifyAdmins(eq("provisioning.publish_completed.v1"), eq("key-1"), eq(messageId),
                eq("fail-admin"), anyMap());
        order.verify(ownerNotifier).notifyOwner(eq("provisioning.publish_completed.v1"), eq("key-1"), eq(messageId),
                eq(projectId), eq("fail-owner"), anyMap());
    }

    @Test
    @DisplayName("stillTellsTheAdminsWhenTheOwnerLookupFails")
    void adminsAreToldEvenWhenOwnerFails() {
        doThrow(new IllegalStateException("project-service down"))
                .when(ownerNotifier).notifyOwner(any(), any(), any(), any(), any(), anyMap());

        assertThatThrownBy(() -> handler().handle(event("FAILED", null)))
                .isInstanceOf(IllegalStateException.class);

        verify(adminNotifier).notifyAdmins(eq("provisioning.publish_completed.v1"), eq("key-1"), eq(messageId),
                eq("fail-admin"), anyMap());
    }

    @Test
    @DisplayName("sendsNothingForRolledBackBecauseTheSpecHasNoNotificationForIt")
    void sendsNothingForRolledBack() {
        handler().handle(event("ROLLED_BACK", null));

        verify(ownerNotifier, never()).notifyOwner(any(), any(), any(), any(), any(), anyMap());
        verify(adminNotifier, never()).notifyAdmins(any(), any(), any(), any(), anyMap());
    }

    @Test
    @DisplayName("ignoresAnUnknownStatusInsteadOfFailing")
    void ignoresUnknownStatus() {
        handler().handle(event("SOMETHING_NEW", null));

        verify(ownerNotifier, never()).notifyOwner(any(), any(), any(), any(), any(), anyMap());
    }
}
