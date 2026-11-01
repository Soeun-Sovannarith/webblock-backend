package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;

/**
 * Core application interface for handling notification dispatch.
 */
public interface NotificationService {

    /**
     * Processes an incoming notification event and orchestrates delivery to the recipient.
     *
     * @param event          Validated notification event payload
     * @param idempotencyKey Stable key for this event and recipient; a repeat with the same key is not sent again
     */
    void processNotification(NotificationEventDto event, String idempotencyKey);
}
