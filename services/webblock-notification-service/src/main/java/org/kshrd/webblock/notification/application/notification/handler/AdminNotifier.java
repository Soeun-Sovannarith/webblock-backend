package org.kshrd.webblock.notification.application.notification.handler;

import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.infrastructure.client.AdminDirectory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.UUID;

/**
 * Sends one notification to every admin. Each admin has its own idempotency key, so when admin 2 fails
 * the retry does not send to admin 1 again (Knock drops the repeat).
 */
@Component
public class AdminNotifier {

    private static final Logger log = LoggerFactory.getLogger(AdminNotifier.class);

    private final AdminDirectory adminDirectory;
    private final NotificationService notificationService;

    public AdminNotifier(AdminDirectory adminDirectory, NotificationService notificationService) {
        this.adminDirectory = adminDirectory;
        this.notificationService = notificationService;
    }

    public void notifyAdmins(String messageType, String eventKey, UUID messageId, String workflowKey, Map<String, Object> data) {
        var admins = adminDirectory.listAdmins();
        if (admins.isEmpty()) {
            log.warn("No admins configured; nothing sent. messageType: [{}], eventId: [{}]", messageType, messageId);
            return;
        }
        for (var admin : admins) {
            var command = new NotificationEventDto(messageId.toString(), workflowKey, "ADMIN",
                    admin.id(), admin.email(), admin.name(), data);
            notificationService.processNotification(command, messageType + ":" + eventKey + ":" + workflowKey + ":" + admin.id());
        }
    }
}
