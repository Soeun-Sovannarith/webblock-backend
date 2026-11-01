package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.common.domain.NotificationDeliveryException;
import org.kshrd.webblock.notification.common.domain.PermanentNotificationDeliveryException;
import org.kshrd.webblock.notification.infrastructure.client.KnockDeliveryClient;
import org.kshrd.webblock.notification.infrastructure.observability.NotificationMetrics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/**
 * Delivers a notification through Knock: tell Knock who the recipient is, then trigger the workflow.
 * The calls to Knock itself are in {@link KnockDeliveryClient} (infrastructure); this class only orders them,
 * adds the event context to the workflow data and logs failures with that context.
 * The Idempotency-Key lets Knock drop a repeat of the same event, so this service keeps no inbox table.
 */
@Service
public class KnockNotificationService implements NotificationService {

    private static final Logger log = LoggerFactory.getLogger(KnockNotificationService.class);

    private final KnockDeliveryClient knock;
    private final NotificationMetrics metrics;

    public KnockNotificationService(KnockDeliveryClient knock, NotificationMetrics metrics) {
        this.knock = knock;
        this.metrics = metrics;
    }

    @Override
    public void processNotification(NotificationEventDto event, String idempotencyKey) {
        Objects.requireNonNull(event, "NotificationEventDto must not be null");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey must not be null");

        log.info("Processing notification. eventId: [{}], eventType: [{}], recipient: [{}]",
                event.eventId(), event.eventType(), event.recipientId());

        try {
            identifyRecipient(event);
            triggerWorkflow(event, idempotencyKey);
        } catch (PermanentNotificationDeliveryException ex) {
            metrics.delivery(event.eventType(), NotificationMetrics.PERMANENT_FAILURE);
            throw ex;
        } catch (RuntimeException ex) {
            // anything else is retried by the listener, so it counts as a temporary failure
            metrics.delivery(event.eventType(), NotificationMetrics.TEMPORARY_FAILURE);
            throw ex;
        }
        metrics.delivery(event.eventType(), NotificationMetrics.SUCCESS);
    }

    private void identifyRecipient(NotificationEventDto event) {
        log.debug("Identifying user in Knock. userId: [{}]", event.recipientId());
        try {
            knock.identifyRecipient(event.recipientId(), event.recipientName(), event.recipientEmail());
        } catch (NotificationDeliveryException ex) {
            logFailure("identify user", event, ex);
            throw ex;
        }
    }

    private void triggerWorkflow(NotificationEventDto event, String idempotencyKey) {
        log.debug("Triggering workflow [{}] for recipient [{}]", event.eventType(), event.recipientId());

        // The event's own data first, then the event context the Knock templates can use
        Map<String, Object> data = new LinkedHashMap<>();
        if (event.data() != null) {
            data.putAll(event.data());
        }
        data.put("eventId", event.eventId());
        data.put("recipientRole", event.recipientRole());

        try {
            String workflowRunId = knock.triggerWorkflow(event.eventType(), event.recipientId(), data, idempotencyKey);
            log.info("Knock workflow [{}] triggered successfully for eventId: [{}], workflowRunId: [{}]",
                    event.eventType(), event.eventId(), workflowRunId);
        } catch (NotificationDeliveryException ex) {
            logFailure("trigger workflow", event, ex);
            throw ex;
        }
    }

    /** Logs the status and the kind of error only: the Knock response body, the email and the stack trace stay out of the log. */
    private void logFailure(String step, NotificationEventDto event, NotificationDeliveryException ex) {
        Integer status = ex.httpStatus();
        if (ex instanceof PermanentNotificationDeliveryException) {
            if (status != null && (status == 401 || status == 403)) {
                log.error("Knock refused our credentials (status {}) while trying to {}. Configuration incident: check the Knock API key and its environment. eventId: [{}]",
                        status, step, event.eventId());
            } else {
                log.error("Knock rejected the request (status {}) while trying to {}; it will not be retried. workflow: [{}], eventId: [{}]",
                        status, step, event.eventType(), event.eventId());
            }
        } else if (status != null) {
            log.warn("Knock error (status {}) while trying to {}; it may be retried. workflow: [{}], eventId: [{}]",
                    status, step, event.eventType(), event.eventId());
        } else {
            String kind = ex.getCause() == null ? ex.getClass().getSimpleName() : ex.getCause().getClass().getSimpleName();
            log.warn("Knock call failed ({}) while trying to {}; it may be retried. workflow: [{}], eventId: [{}]",
                    kind, step, event.eventType(), event.eventId());
        }
    }
}
