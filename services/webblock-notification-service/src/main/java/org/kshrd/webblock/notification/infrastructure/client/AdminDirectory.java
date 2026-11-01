package org.kshrd.webblock.notification.infrastructure.client;

import java.util.List;

/**
 * Who the platform admins are. The target is the Keycloak admin list; today {@link ConfigAdminDirectory} reads a config list.
 */
public interface AdminDirectory {

    List<AdminRecipient> listAdmins();

    record AdminRecipient(String id, String name, String email) {
        @Override
        public String toString() {
            return "AdminRecipient[id=" + id + "]";
        }
    }
}
