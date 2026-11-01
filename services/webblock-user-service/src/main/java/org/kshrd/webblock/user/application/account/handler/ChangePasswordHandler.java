package org.kshrd.webblock.user.application.account.handler;

import org.kshrd.webblock.user.application.account.request.ChangePasswordRequest;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

// PUT /me/password (Settings › Password):
// - user with a password: the current password must be right (Keycloak checks it), then the new one is set
// - Google-only user: sets a first password (then email + password sign-in also works)
// After the change, every OTHER browser/device is signed out; this one stays signed in.
@Component
public class ChangePasswordHandler {

    private static final Logger log = LoggerFactory.getLogger(ChangePasswordHandler.class);

    private final GetMyAccountHandler getMyAccount;
    private final KeycloakAdminClient keycloak;

    public ChangePasswordHandler(GetMyAccountHandler getMyAccount, KeycloakAdminClient keycloak) {
        this.getMyAccount = getMyAccount;
        this.keycloak = keycloak;
    }

    public void handle(CurrentUser caller, ChangePasswordRequest request) {
        if (!request.newPassword().equals(request.confirmPassword())) {
            throw new ApiException(ErrorCode.PASSWORD_MISMATCH);
        }
        // Account row exists and is not suspended
        getMyAccount.handle(caller);

        String id = caller.keycloakUserId();
        KeycloakAdminClient.KeycloakUser user = keycloak.findUserById(id)
                .orElseThrow(() -> new ApiException(ErrorCode.USER_NOT_FOUND));

        if (request.newPassword().equalsIgnoreCase(user.email())) {
            throw new ApiException(ErrorCode.PASSWORD_TOO_WEAK); // realm policy: not the email
        }

        boolean hadPassword = keycloak.hasPassword(id);
        if (hadPassword) {
            String current = request.currentPassword();
            if (current == null || current.isEmpty()) {
                throw new ApiException(ErrorCode.CURRENT_PASSWORD_REQUIRED);
            }
            if (!keycloak.checkPassword(user.email(), current)) {
                throw new ApiException(ErrorCode.CURRENT_PASSWORD_WRONG);
            }
        }

        keycloak.setPassword(id, request.newPassword());
        keycloak.logoutOtherSessions(id, caller.sessionId());
        log.info("Password {} for user_id={}", hadPassword ? "changed" : "set", id);
    }
}
