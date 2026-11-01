package org.kshrd.webblock.notification.infrastructure.client;

import java.util.Map;

/**
 * The calls this service makes to Knock: tell Knock who the recipient is, then trigger a workflow for them.
 * Knock is an outside system, so the SDK stays behind this interface (implementation: {@code KnockSdkDeliveryClient}).
 * Failures are thrown as {@code NotificationDeliveryException} (temporary) or {@code PermanentNotificationDeliveryException}
 * (a retry cannot help). The email and the Knock response body never appear in an exception message.
 */
public interface KnockDeliveryClient {

    /** Creates or updates the recipient in Knock (identify / upsert). */
    void identifyRecipient(String recipientId, String name, String email);

    /**
     * Triggers a workflow for one recipient. The same {@code idempotencyKey} lets Knock drop a repeat of the same trigger.
     *
     * @return Knock's workflow run id
     */
    String triggerWorkflow(String workflowKey, String recipientId, Map<String, Object> data, String idempotencyKey);
}
