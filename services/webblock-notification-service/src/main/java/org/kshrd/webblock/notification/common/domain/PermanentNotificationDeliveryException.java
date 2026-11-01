package org.kshrd.webblock.notification.common.domain;

/**
 * Knock answered with an error that a retry cannot fix (bad request, wrong credentials, missing workflow).
 * It goes to the dead-letter queue without further attempts.
 */
public class PermanentNotificationDeliveryException extends NotificationDeliveryException implements PermanentFailure {

    public PermanentNotificationDeliveryException(String message, Throwable cause) {
        super(message, cause);
    }

    public PermanentNotificationDeliveryException(String message, Throwable cause, Integer httpStatus) {
        super(message, cause, httpStatus);
    }
}
