package org.kshrd.webblock.user.infrastructure.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

// webblock.keycloak.* in application.yml: how user-service talks to Keycloak.
@ConfigurationProperties(prefix = "webblock.keycloak")
public record KeycloakAdminProperties(
        String serverUrl,     // where tokens come from, e.g. https://auth.roa-dev.digital
        String adminUrl,      // where the Admin API is (can be the same host, or the admin host)
        String realm,         // webblock
        String clientId,      // webblock-auth-admin (service account with manage-users, view-users)
        String clientSecret,  // KEYCLOAK_ADMIN_CLIENT_SECRET; never commit it
        // Only when the Admin API sits behind Cloudflare Access (dev, from a laptop): a Cloudflare service token.
        // Empty = no extra headers (inside the cluster, user-service calls Keycloak directly).
        String cfAccessClientId,
        String cfAccessClientSecret,
        // Change password: a client with ONLY "Direct access grants" ON, used to check the current password.
        String passwordCheckClientId,      // webblock-password-check
        String passwordCheckClientSecret) { // KEYCLOAK_PASSWORD_CHECK_CLIENT_SECRET; never commit it

    public boolean usesCloudflareAccess() {
        return cfAccessClientId != null && !cfAccessClientId.isBlank()
                && cfAccessClientSecret != null && !cfAccessClientSecret.isBlank();
    }

    // Never print the secrets (for example in a startup log)
    @Override
    public String toString() {
        return "KeycloakAdminProperties[serverUrl=" + serverUrl + ", adminUrl=" + adminUrl + ", realm=" + realm
                + ", clientId=" + clientId + ", passwordCheckClientId=" + passwordCheckClientId + "]";
    }
}
