package org.kshrd.webblock.notification.common.domain;

/**
 * A temporary failure while handing a notification to Knock (429, 5xx, network error, unknown error): the listener retries it
 * a limited number of times. A failure that retrying cannot fix is a {@link PermanentNotificationDeliveryException}.
 * The message never holds the email or the Knock response body.
 */
public class NotificationDeliveryException extends RuntimeException {

    private final Integer httpStatus;

    public NotificationDeliveryException(String message, Throwable cause) {
        this(message, cause, null);
    }

    /** @param httpStatus the status Knock answered with, or null when there was no HTTP answer (network error, unknown error) */
    public NotificationDeliveryException(String message, Throwable cause, Integer httpStatus) {
        super(message, cause);
        this.httpStatus = httpStatus;
    }

    /** The HTTP status Knock answered with, or null when there was none. */
    public Integer httpStatus() {
        return httpStatus;
    }
}
