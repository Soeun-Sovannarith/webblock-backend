package org.kshrd.webblock.notification.application.notification.handler;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.TestProperties;
import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class HandlePaymentDueHandlerTest {

    @Mock
    private OwnerNotifier ownerNotifier;

    @Test
    @DisplayName("tellsTheOwnerByUserIdWithTheAmountAndDueDate")
    void paymentDueTellsTheOwnerWithoutAProjectLookup() {
        UUID messageId = UUID.randomUUID();
        UUID userId = UUID.randomUUID();
        UUID subscriptionId = UUID.randomUUID();
        UUID planId = UUID.randomUUID();
        Instant dueAt = Instant.parse("2026-11-06T00:00:00Z");

        new HandlePaymentDueHandler(ownerNotifier, TestProperties.PROPERTIES)
                .handle(new PaymentDueEvent(messageId, "sub-1:2026-11-06", userId, subscriptionId, planId,
                        new BigDecimal("9.90"), dueAt));

        verify(ownerNotifier).notifyUser(eq("payment.payment_due.v1"), eq("sub-1:2026-11-06"), eq(messageId),
                eq(userId.toString()), eq("due-owner"),
                eq(Map.of("subscriptionId", subscriptionId.toString(), "planId", planId.toString(),
                        "amountUsd", "9.90", "dueAt", "2026-11-06T00:00:00Z")));
    }
}
