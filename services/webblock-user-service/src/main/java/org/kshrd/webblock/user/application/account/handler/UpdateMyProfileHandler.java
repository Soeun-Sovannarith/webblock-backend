package org.kshrd.webblock.user.application.account.handler;

import org.kshrd.webblock.user.application.account.mapper.AccountMapper;
import org.kshrd.webblock.user.application.account.request.UpdateProfileRequest;
import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.account.model.AccountUser;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.kshrd.webblock.user.infrastructure.persistence.account.AccountUserRepository;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;

// PUT /me: change the full name (Settings › Profile).
// Saved in our table AND in Keycloak (firstName), so the next token and the Keycloak pages show it too.
// If Keycloak fails, the transaction rolls back: both stay on the old name.
@Component
public class UpdateMyProfileHandler {

    private final AccountUserRepository repository;
    private final GetMyAccountHandler getMyAccount;
    private final KeycloakAdminClient keycloak;
    private final Clock clock;

    public UpdateMyProfileHandler(AccountUserRepository repository, GetMyAccountHandler getMyAccount,
                                  KeycloakAdminClient keycloak, Clock clock) {
        this.repository = repository;
        this.getMyAccount = getMyAccount;
        this.keycloak = keycloak;
        this.clock = clock;
    }

    @Transactional
    public AccountResponse handle(CurrentUser caller, UpdateProfileRequest request) {
        // Makes sure the row exists and the account is not suspended
        getMyAccount.handle(caller);

        AccountUser user = repository
                .findById(caller.userId())
                .orElseThrow(() -> new ApiException(ErrorCode.INTERNAL_ERROR));

        String fullName = request.fullName().trim();
        user.changeDisplayName(fullName, clock.instant());
        keycloak.updateFullName(caller.keycloakUserId(), fullName);
        return AccountMapper.toResponse(user);
    }
}
