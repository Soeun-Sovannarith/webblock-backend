package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;

/**
 * provisioning.publish_completed.v1:
 * LIVE -> owner "deployment succeeds"; FAILED -> owner "deployment fails" and admin "deployment fails".
 * ROLLED_BACK and unknown values are ignored: the spec has no notification for them yet (open question).
 */
@Service
public class HandlePublishCompletedHandler {

    private static final Logger log = LoggerFactory.getLogger(HandlePublishCompletedHandler.class);

    static final String MESSAGE_TYPE = "provisioning.publish_completed.v1";

    private final OwnerNotifier ownerNotifier;
    private final AdminNotifier adminNotifier;
    private final NotificationProperties properties;

    public HandlePublishCompletedHandler(OwnerNotifier ownerNotifier, AdminNotifier adminNotifier,
                                         NotificationProperties properties) {
        this.ownerNotifier = ownerNotifier;
        this.adminNotifier = adminNotifier;
        this.properties = properties;
    }

    public void handle(PublishCompletedEvent event) {
        var workflows = properties.workflows();
        Map<String, Object> data = new HashMap<>();
        data.put("releaseId", event.releaseId().toString());

        switch (event.status()) {
            case "LIVE" -> ownerNotifier.notifyOwner(MESSAGE_TYPE, event.eventKey(), event.messageId(),
                    event.projectId(), workflows.deploymentSucceeded(), data);
            case "FAILED" -> {
                if (event.failureCode() != null) {
                    data.put("failureCode", event.failureCode());   // sanitized by the sender
                }
                // Admins first: the owner needs lookups in project-service and user-service. If those stay down the
                // message ends in the DLQ, and the admins must already know the deploy failed.
                Map<String, Object> adminData = new HashMap<>(data);
                adminData.put("projectId", event.projectId().toString());
                adminNotifier.notifyAdmins(MESSAGE_TYPE, event.eventKey(), event.messageId(),
                        workflows.deploymentFailedAdmin(), adminData);
                ownerNotifier.notifyOwner(MESSAGE_TYPE, event.eventKey(), event.messageId(),
                        event.projectId(), workflows.deploymentFailedOwner(), data);
            }
            default -> log.info("No notification for publish status [{}]. eventId: [{}]", event.status(), event.messageId());
        }
    }
}
