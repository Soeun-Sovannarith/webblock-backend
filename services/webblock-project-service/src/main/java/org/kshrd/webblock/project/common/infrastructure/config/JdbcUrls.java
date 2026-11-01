package org.kshrd.webblock.project.common.infrastructure.config;

import java.util.List;

/**
 * pgjdbc lets a JDBC URL parameter replace a driver property of the same name, which would undo a setting the
 * service's connections rely on. Used by ProfileGuard at start-up and by the migration job before it connects.
 */
public final class JdbcUrls {

    /**
     * No server error detail in messages (the pools and both migrations), a socket timeout (the pools), and options,
     * which a URL could use to start sessions with other settings.
     */
    private static final List<String> GUARDED_DRIVER_PROPERTIES =
            List.of("logServerErrorDetail", "socketTimeout", "options");

    private JdbcUrls() {}

    /**
     * Refuses a URL whose query sets a guarded driver property, in any case. The message names the setting and the
     * parameter, never the URL, which may hold a password.
     */
    public static void refuseDriverPropertyOverrides(String setting, String url) {
        int query = url == null ? -1 : url.indexOf('?');
        if (query < 0) {
            return;
        }
        for (String parameter : url.substring(query + 1).split("&", -1)) {
            String key = parameter.split("=", 2)[0].strip();
            for (String property : GUARDED_DRIVER_PROPERTIES) {
                if (property.equalsIgnoreCase(key)) {
                    throw new IllegalStateException(setting + " must not set the driver property " + property
                            + ": it would replace the setting the service relies on");
                }
            }
        }
    }
}
