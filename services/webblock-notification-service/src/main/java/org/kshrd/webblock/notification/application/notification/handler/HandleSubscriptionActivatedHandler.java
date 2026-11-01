package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.stereotype.Service;

import java.util.Map;

/** payment.subscription_activated.v1 -> admin: "a new subscription becomes Active after payment" (Web). */
@Service
public class HandleSubscriptionActivatedHandler {

    static final String MESSAGE_TYPE = "payment.subscription_activated.v1";

    private final AdminNotifier adminNotifier;
    private final NotificationProperties properties;

    public HandleSubscriptionActivatedHandler(AdminNotifier adminNotifier, NotificationProperties properties) {
        this.adminNotifier = adminNotifier;
        this.properties = properties;
    }

    public void handle(SubscriptionActivatedEvent event) {
        adminNotifier.notifyAdmins(MESSAGE_TYPE, event.eventKey(), event.messageId(),
                properties.workflows().subscriptionActivatedAdmin(),
                Map.of("subscriptionId", event.subscriptionId().toString(),
                        "clientUserId", event.clientUserId().toString(),
                        "planId", event.planId().toString()));
    }
}
