package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * payment.payment_due.v1 -> owner: "monthly payment is due" (Email; Mobile later).
 * Payment-service decides when to send it. The subscription belongs to the user, so the owner is found by user_id,
 * with no project lookup.
 */
@Service
public class HandlePaymentDueHandler {

    static final String MESSAGE_TYPE = "payment.payment_due.v1";

    private final OwnerNotifier ownerNotifier;
    private final NotificationProperties properties;

    public HandlePaymentDueHandler(OwnerNotifier ownerNotifier, NotificationProperties properties) {
        this.ownerNotifier = ownerNotifier;
        this.properties = properties;
    }

    public void handle(PaymentDueEvent event) {
        ownerNotifier.notifyUser(MESSAGE_TYPE, event.eventKey(), event.messageId(), event.userId().toString(),
                properties.workflows().paymentDueOwner(),
                Map.of("subscriptionId", event.subscriptionId().toString(),
                        "planId", event.planId().toString(),
                        "amountUsd", event.amountUsd().toPlainString(),
                        "dueAt", event.dueAt().toString()));
    }
}
