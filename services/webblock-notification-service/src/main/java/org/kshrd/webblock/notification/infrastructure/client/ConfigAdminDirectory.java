package org.kshrd.webblock.notification.infrastructure.client;

import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.stereotype.Component;

import java.util.List;

/** Admins listed in "webblock.notification.admins". Replace with a Keycloak lookup when its credentials are set up. */
@Component
public class ConfigAdminDirectory implements AdminDirectory {

    private final List<AdminRecipient> admins;

    public ConfigAdminDirectory(NotificationProperties properties) {
        this.admins = properties.admins().stream()
                .map(a -> new AdminRecipient(a.id(), a.name(), a.email()))
                .toList();
    }

    @Override
    public List<AdminRecipient> listAdmins() {
        return admins;
    }
}
