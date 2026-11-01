package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.infrastructure.client.ProjectOwnerClient;
import org.kshrd.webblock.notification.infrastructure.client.UserContactClient;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Finds the owner of a project (project-service), then their contact (user-service), then sends once
 * (Knock drops a repeat of the same idempotency key).
 * If a lookup fails the exception goes up, the message is retried and finally dead-lettered (fail closed).
 */
@Component
public class OwnerNotifier {

    private final ProjectOwnerClient projectOwnerClient;
    private final UserContactClient userContactClient;
    private final NotificationService notificationService;

    public OwnerNotifier(ProjectOwnerClient projectOwnerClient,
                         UserContactClient userContactClient,
                         NotificationService notificationService) {
        this.projectOwnerClient = projectOwnerClient;
        this.userContactClient = userContactClient;
        this.notificationService = notificationService;
    }

    public void notifyOwner(String messageType, String eventKey, UUID messageId, UUID projectId,
                            String workflowKey, Map<String, Object> data) {
        var owner = projectOwnerClient.getProjectOwner(projectId);
        var contact = userContactClient.getUserContact(owner.ownerUserId());

        Map<String, Object> payload = new HashMap<>(data);
        payload.put("projectId", projectId.toString());
        payload.put("projectName", owner.projectName());

        var command = new NotificationEventDto(messageId.toString(), workflowKey, "OWNER",
                owner.ownerUserId(), contact.email(), contact.fullName(), payload);
        notificationService.processNotification(command, messageType + ":" + eventKey + ":" + workflowKey + ":" + owner.ownerUserId());
    }

    /** For events that already carry the user (e.g. payment due): no project lookup, only the contact lookup. */
    public void notifyUser(String messageType, String eventKey, UUID messageId, String userId,
                           String workflowKey, Map<String, Object> data) {
        var contact = userContactClient.getUserContact(userId);

        var command = new NotificationEventDto(messageId.toString(), workflowKey, "OWNER",
                userId, contact.email(), contact.fullName(), new HashMap<>(data));
        notificationService.processNotification(command, messageType + ":" + eventKey + ":" + workflowKey + ":" + userId);
    }
}
