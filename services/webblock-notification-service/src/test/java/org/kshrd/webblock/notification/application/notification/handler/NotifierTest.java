package org.kshrd.webblock.notification.application.notification.handler;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.infrastructure.client.AdminDirectory;
import org.kshrd.webblock.notification.infrastructure.client.ProjectOwnerClient;
import org.kshrd.webblock.notification.infrastructure.client.UserContactClient;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class NotifierTest {

    @Mock
    private ProjectOwnerClient projectOwnerClient;

    @Mock
    private UserContactClient userContactClient;

    @Mock
    private AdminDirectory adminDirectory;

    @Mock
    private NotificationService notificationService;

    private final UUID messageId = UUID.randomUUID();
    private final UUID projectId = UUID.randomUUID();

    @Test
    @DisplayName("findsTheOwnerAndTheirContactThenSendsOnce")
    void ownerIsLookedUpThroughProjectThenUser() {
        when(projectOwnerClient.getProjectOwner(projectId))
                .thenReturn(new ProjectOwnerClient.ProjectOwner("owner-1", "Sokha Cafe"));
        when(userContactClient.getUserContact("owner-1"))
                .thenReturn(new UserContactClient.UserContact("owner@example.com", "Sokha", "ACTIVE"));

        new OwnerNotifier(projectOwnerClient, userContactClient, notificationService)
                .notifyOwner("t.v1", "key-1", messageId, projectId, "wf-owner", Map.of("releaseId", "r1"));

        ArgumentCaptor<NotificationEventDto> command = ArgumentCaptor.forClass(NotificationEventDto.class);
        verify(notificationService).processNotification(command.capture(),
                org.mockito.ArgumentMatchers.eq("t.v1:key-1:wf-owner:owner-1"));
        assertThat(command.getValue().recipientId()).isEqualTo("owner-1");
        assertThat(command.getValue().recipientEmail()).isEqualTo("owner@example.com");
        assertThat(command.getValue().eventType()).isEqualTo("wf-owner");
        assertThat(command.getValue().data()).containsEntry("projectName", "Sokha Cafe")
                .containsEntry("releaseId", "r1");
    }

    @Test
    @DisplayName("looksUpOnlyTheContactWhenTheEventAlreadyCarriesTheUser")
    void userIsNotifiedWithoutAProjectLookup() {
        when(userContactClient.getUserContact("user-1"))
                .thenReturn(new UserContactClient.UserContact("user@example.com", "Sokha", "ACTIVE"));

        new OwnerNotifier(projectOwnerClient, userContactClient, notificationService)
                .notifyUser("t.v1", "key-1", messageId, "user-1", "wf-due", Map.of("amountUsd", "9.90"));

        ArgumentCaptor<NotificationEventDto> command = ArgumentCaptor.forClass(NotificationEventDto.class);
        verify(notificationService).processNotification(command.capture(),
                org.mockito.ArgumentMatchers.eq("t.v1:key-1:wf-due:user-1"));
        assertThat(command.getValue().recipientId()).isEqualTo("user-1");
        assertThat(command.getValue().recipientEmail()).isEqualTo("user@example.com");
        assertThat(command.getValue().eventType()).isEqualTo("wf-due");
        assertThat(command.getValue().data()).containsEntry("amountUsd", "9.90");
        verifyNoInteractions(projectOwnerClient);
    }

    @Test
    @DisplayName("sendsNothingAndLetsTheErrorOutWhenTheOwnerLookupFails")
    void ownerLookupFailureStopsTheDelivery() {
        when(projectOwnerClient.getProjectOwner(projectId)).thenThrow(new IllegalStateException("project-service down"));

        assertThatThrownBy(() -> new OwnerNotifier(projectOwnerClient, userContactClient, notificationService)
                .notifyOwner("t.v1", "key-1", messageId, projectId, "wf-owner", Map.of()))
                .isInstanceOf(IllegalStateException.class);

        verify(notificationService, never()).processNotification(any(), any());
    }

    @Test
    @DisplayName("sendsOneNotificationPerAdminWithADifferentKeyEach")
    void everyAdminGetsItsOwnDelivery() {
        when(adminDirectory.listAdmins()).thenReturn(List.of(
                new AdminDirectory.AdminRecipient("a1", "Admin One", "a1@example.com"),
                new AdminDirectory.AdminRecipient("a2", "Admin Two", "a2@example.com")));

        new AdminNotifier(adminDirectory, notificationService)
                .notifyAdmins("t.v1", "key-1", messageId, "wf-admin", Map.of());

        ArgumentCaptor<String> keys = ArgumentCaptor.forClass(String.class);
        verify(notificationService, times(2)).processNotification(any(), keys.capture());
        assertThat(keys.getAllValues()).containsExactly("t.v1:key-1:wf-admin:a1", "t.v1:key-1:wf-admin:a2");
    }

    @Test
    @DisplayName("sendsNothingWhenNoAdminIsConfigured")
    void noAdminsMeansNoDelivery() {
        when(adminDirectory.listAdmins()).thenReturn(List.of());

        new AdminNotifier(adminDirectory, notificationService).notifyAdmins("t.v1", "key-1", messageId, "wf-admin", Map.of());

        verify(notificationService, never()).processNotification(any(), any());
    }
}
