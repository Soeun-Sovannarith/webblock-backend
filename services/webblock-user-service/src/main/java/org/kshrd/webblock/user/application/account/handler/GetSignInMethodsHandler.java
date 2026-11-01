package org.kshrd.webblock.user.application.account.handler;

import org.kshrd.webblock.user.application.account.response.SignInMethodsResponse;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.springframework.stereotype.Component;

// GET /me/sign-in-methods: does the user have a password, and is Google connected? (asks Keycloak)
@Component
public class GetSignInMethodsHandler {

    private final KeycloakAdminClient keycloak;

    public GetSignInMethodsHandler(KeycloakAdminClient keycloak) {
        this.keycloak = keycloak;
    }

    public SignInMethodsResponse handle(CurrentUser caller) {
        String id = caller.keycloakUserId();
        return new SignInMethodsResponse(keycloak.hasPassword(id), keycloak.googleEmail(id).orElse(null));
    }
}
