package org.kshrd.webblock.notification;

import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;

import java.time.Duration;
import java.util.List;

/** NotificationProperties for unit tests: each workflow key is a short readable name. */
public final class TestProperties {

    public static final NotificationProperties PROPERTIES = new NotificationProperties(
            new NotificationProperties.Workflows("gen", "ok", "fail-owner", "fail-admin", "expired", "activated",
                    "stuck-admin", "crash-owner", "crash-admin", "limit-owner", "limit-admin", "down-admin", "due-owner"),
            List.of(),
            new NotificationProperties.Stub(null, null, null, null),
            new NotificationProperties.Knock(null, Duration.ofHours(1)),
            new NotificationProperties.Alertmanager(null));

    private TestProperties() {
    }
}
