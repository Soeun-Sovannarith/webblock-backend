package org.kshrd.webblock.notification.application.alert.handler;

import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest.Alert;
import org.kshrd.webblock.notification.application.notification.handler.AdminNotifier;
import org.kshrd.webblock.notification.application.notification.handler.OwnerNotifier;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Turns Alertmanager alerts into notifications (business-rules.md, Notifications table).
 * Alertmanager decides WHEN something is wrong; this class only maps the alert to who is told.
 * <pre>
 * alertname                     labels read                                          owner             admin
 * GenerationStuck               project_id, generation_request_id (both optional)    -                 generation stuck
 * WebsiteDown                   project_id                                           website crashed   website crashed
 * WebsiteResourceLimitExceeded  project_id, resource_type, current_usage, plan_limit resource limit    resource limit
 * PlatformServiceDown           service                                              -                 service down
 * </pre>
 * Only "firing" alerts notify (the spec has no "back to normal" notification); unknown alert names are ignored.
 * One incident (alert name + fingerprint + start time) always gets the same idempotency key, so Alertmanager's
 * repeat_interval re-sends and webhook retries do not notify again. A new incident starts at a new time and notifies again.
 * Admins are told before the owner: the owner needs lookups in project-service and user-service that can fail.
 */
@Service
public class HandleAlertmanagerWebhookHandler {

    private static final Logger log = LoggerFactory.getLogger(HandleAlertmanagerWebhookHandler.class);

    static final String MESSAGE_TYPE = "alertmanager.alert";

    public static final String GENERATION_STUCK = "GenerationStuck";
    public static final String WEBSITE_DOWN = "WebsiteDown";
    public static final String RESOURCE_LIMIT_EXCEEDED = "WebsiteResourceLimitExceeded";
    public static final String PLATFORM_SERVICE_DOWN = "PlatformServiceDown";

    // Labels copied into the Knock data, with their camelCase key. Annotations are free text and are not passed on.
    private static final Map<String, String> DATA_LABELS = Map.of(
            "project_id", "projectId",
            "generation_request_id", "generationRequestId",
            "service", "service",
            "resource_type", "resourceType",
            "current_usage", "currentUsage",
            "plan_limit", "planLimit");

    private final OwnerNotifier ownerNotifier;
    private final AdminNotifier adminNotifier;
    private final NotificationProperties properties;

    public HandleAlertmanagerWebhookHandler(OwnerNotifier ownerNotifier, AdminNotifier adminNotifier,
                                            NotificationProperties properties) {
        this.ownerNotifier = ownerNotifier;
        this.adminNotifier = adminNotifier;
        this.properties = properties;
    }

    public void handle(AlertmanagerWebhookRequest request) {
        for (Alert alert : request.alerts()) {
            handleOne(alert);
        }
    }

    private void handleOne(Alert alert) {
        if (!"firing".equals(alert.status())) {
            return;
        }
        String alertName = alert.labels().get("alertname");
        if (isBlank(alertName) || isBlank(alert.fingerprint()) || isBlank(alert.startsAt())) {
            log.warn("Alert without alertname, fingerprint or startsAt skipped");
            return;
        }
        var workflows = properties.workflows();
        switch (alertName) {
            case GENERATION_STUCK -> notifyAdmins(alert, alertName, workflows.generationStuckAdmin());
            case WEBSITE_DOWN -> {
                notifyAdmins(alert, alertName, workflows.websiteCrashedAdmin());
                notifyOwner(alert, alertName, workflows.websiteCrashedOwner());
            }
            case RESOURCE_LIMIT_EXCEEDED -> {
                notifyAdmins(alert, alertName, workflows.resourceLimitExceededAdmin());
                notifyOwner(alert, alertName, workflows.resourceLimitExceededOwner());
            }
            case PLATFORM_SERVICE_DOWN -> notifyAdmins(alert, alertName, workflows.platformServiceDownAdmin());
            default -> log.info("No notification for alert [{}]", alertName);
        }
    }

    private void notifyAdmins(Alert alert, String alertName, String workflowKey) {
        String eventKey = eventKey(alert, alertName);
        adminNotifier.notifyAdmins(MESSAGE_TYPE, eventKey, messageId(eventKey), workflowKey, data(alert, alertName));
    }

    private void notifyOwner(Alert alert, String alertName, String workflowKey) {
        UUID projectId = parseUuid(alert.labels().get("project_id"));
        if (projectId == null) {
            log.warn("Alert [{}] has no valid project_id label; owner not notified", alertName);
            return;
        }
        String eventKey = eventKey(alert, alertName);
        ownerNotifier.notifyOwner(MESSAGE_TYPE, eventKey, messageId(eventKey), projectId, workflowKey, data(alert, alertName));
    }

    private static String eventKey(Alert alert, String alertName) {
        return alertName + ":" + alert.fingerprint() + ":" + alert.startsAt();
    }

    /** Same incident -> same id, so the eventId Knock shows is stable across re-sends. */
    private static UUID messageId(String eventKey) {
        return UUID.nameUUIDFromBytes(eventKey.getBytes(StandardCharsets.UTF_8));
    }

    private static Map<String, Object> data(Alert alert, String alertName) {
        Map<String, Object> data = new HashMap<>();
        data.put("alertName", alertName);
        DATA_LABELS.forEach((label, key) -> {
            String value = alert.labels().get(label);
            if (!isBlank(value)) {
                data.put(key, value);
            }
        });
        return data;
    }

    private static UUID parseUuid(String value) {
        if (isBlank(value)) {
            return null;
        }
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
