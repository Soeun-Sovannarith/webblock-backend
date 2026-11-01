package org.kshrd.webblock.notification;

import org.kshrd.webblock.notification.application.alert.handler.HandleAlertmanagerWebhookHandler;
import org.kshrd.webblock.notification.application.notification.handler.AdminNotifier;
import org.kshrd.webblock.notification.application.notification.handler.HandleHostingEntitlementEndedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandlePaymentDueHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandlePublishCompletedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandleSourcePublishedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandleSubscriptionActivatedHandler;
import org.kshrd.webblock.notification.application.notification.handler.NotificationService;
import org.kshrd.webblock.notification.application.notification.handler.OwnerNotifier;
import org.kshrd.webblock.notification.infrastructure.client.AdminDirectory;
import org.kshrd.webblock.notification.infrastructure.client.ProjectOwnerClient;
import org.kshrd.webblock.notification.infrastructure.client.UserContactClient;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * The real handlers and notifiers wired on top of any {@link NotificationService}, with fake lookups (one owner, one admin,
 * all on example.test addresses). Test-only. Lets a test follow an event from the handler down to where it leaves the service.
 */
public final class TestWiring {

    /** A UUID because payment_due identifies the owner by the event's own user_id. */
    public static final String OWNER_ID = "00000000-0000-0000-0000-000000000001";
    public static final String ADMIN_ID = "admin-1";

    public final OwnerNotifier ownerNotifier;
    public final AdminNotifier adminNotifier;

    public TestWiring(NotificationService sender) {
        ProjectOwnerClient projectOwnerClient = mock(ProjectOwnerClient.class);
        UserContactClient userContactClient = mock(UserContactClient.class);
        AdminDirectory adminDirectory = mock(AdminDirectory.class);
        when(projectOwnerClient.getProjectOwner(any())).thenReturn(new ProjectOwnerClient.ProjectOwner(OWNER_ID, "Test Cafe"));
        when(userContactClient.getUserContact(anyString()))
                .thenReturn(new UserContactClient.UserContact("owner@example.test", "Test Owner", "ACTIVE"));
        when(adminDirectory.listAdmins())
                .thenReturn(List.of(new AdminDirectory.AdminRecipient(ADMIN_ID, "Test Admin", "admin-1@example.test")));
        this.ownerNotifier = new OwnerNotifier(projectOwnerClient, userContactClient, sender);
        this.adminNotifier = new AdminNotifier(adminDirectory, sender);
    }

    public HandleSourcePublishedHandler sourcePublished() {
        return new HandleSourcePublishedHandler(ownerNotifier, TestProperties.PROPERTIES);
    }

    public HandlePublishCompletedHandler publishCompleted() {
        return new HandlePublishCompletedHandler(ownerNotifier, adminNotifier, TestProperties.PROPERTIES);
    }

    public HandleHostingEntitlementEndedHandler hostingEntitlementEnded() {
        return new HandleHostingEntitlementEndedHandler(adminNotifier, TestProperties.PROPERTIES);
    }

    public HandleSubscriptionActivatedHandler subscriptionActivated() {
        return new HandleSubscriptionActivatedHandler(adminNotifier, TestProperties.PROPERTIES);
    }

    public HandlePaymentDueHandler paymentDue() {
        return new HandlePaymentDueHandler(ownerNotifier, TestProperties.PROPERTIES);
    }

    public HandleAlertmanagerWebhookHandler alerts() {
        return new HandleAlertmanagerWebhookHandler(ownerNotifier, adminNotifier, TestProperties.PROPERTIES);
    }
}
