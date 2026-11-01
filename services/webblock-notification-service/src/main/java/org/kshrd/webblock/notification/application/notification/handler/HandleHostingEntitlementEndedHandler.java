package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.HostingEntitlementEndedEvent;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * payment.hosting_entitlement_ended.v1 -> admin: "a user's subscription expires" (Web).
 * It is NOT "monthly payment due": that reminder is payment.payment_due.v1 (HandlePaymentDueHandler).
 */
@Service
public class HandleHostingEntitlementEndedHandler {

    static final String MESSAGE_TYPE = "payment.hosting_entitlement_ended.v1";

    private final AdminNotifier adminNotifier;
    private final NotificationProperties properties;

    public HandleHostingEntitlementEndedHandler(AdminNotifier adminNotifier, NotificationProperties properties) {
        this.adminNotifier = adminNotifier;
        this.properties = properties;
    }

    public void handle(HostingEntitlementEndedEvent event) {
        adminNotifier.notifyAdmins(MESSAGE_TYPE, event.eventKey(), event.messageId(),
                properties.workflows().subscriptionExpiredAdmin(),
                Map.of("userId", event.userId().toString()));
    }
}
