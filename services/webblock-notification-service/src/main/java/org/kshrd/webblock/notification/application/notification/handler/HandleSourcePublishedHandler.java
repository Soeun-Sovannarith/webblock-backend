package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * project.source_published.v1 -> owner: "website generation completes" (Web + Mobile, set in the Knock workflow).
 * Assumption to confirm with the team: source_published means generation finished (docs/notification-service-report.md).
 */
@Service
public class HandleSourcePublishedHandler {

    static final String MESSAGE_TYPE = "project.source_published.v1";

    private final OwnerNotifier ownerNotifier;
    private final NotificationProperties properties;

    public HandleSourcePublishedHandler(OwnerNotifier ownerNotifier, NotificationProperties properties) {
        this.ownerNotifier = ownerNotifier;
        this.properties = properties;
    }

    public void handle(SourcePublishedEvent event) {
        // The same fact re-sent with a new message_id has the same (project, generation request).
        String eventKey = event.generationRequestId() != null
                ? event.projectId() + ":" + event.generationRequestId()
                : event.messageId().toString();
        ownerNotifier.notifyOwner(MESSAGE_TYPE, eventKey, event.messageId(), event.projectId(),
                properties.workflows().generationCompleted(), Map.of());
    }
}
