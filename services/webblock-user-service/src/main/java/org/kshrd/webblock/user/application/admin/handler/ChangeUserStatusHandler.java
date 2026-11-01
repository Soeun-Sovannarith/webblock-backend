package org.kshrd.webblock.user.application.admin.handler;

import org.kshrd.webblock.user.application.account.mapper.AccountMapper;
import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.application.admin.request.ChangeUserStatusRequest;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.account.model.AccountStatus;
import org.kshrd.webblock.user.domain.account.model.AccountUser;
import org.kshrd.webblock.user.domain.account.model.AccountUserStatusChange;
import org.kshrd.webblock.user.domain.account.model.StatusReason;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.kshrd.webblock.user.infrastructure.persistence.account.AccountUserRepository;
import org.kshrd.webblock.user.infrastructure.persistence.account.AccountUserStatusChangeRepository;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.util.UUID;

// PUT /admin/users/{userId}/status: suspend or activate an account.
// Suspend = status SUSPENDED + history row + Keycloak user disabled + signed out everywhere.
// Activate = status ACTIVE + history row + Keycloak user enabled.
// If Keycloak fails, the transaction rolls back (our table and Keycloak never disagree).
@Component
public class ChangeUserStatusHandler {

    private static final Logger log = LoggerFactory.getLogger(ChangeUserStatusHandler.class);

    private final AccountUserRepository users;
    private final AccountUserStatusChangeRepository history;
    private final KeycloakAdminClient keycloak;
    private final Clock clock;

    public ChangeUserStatusHandler(AccountUserRepository users, AccountUserStatusChangeRepository history,
                                   KeycloakAdminClient keycloak, Clock clock) {
        this.users = users;
        this.history = history;
        this.keycloak = keycloak;
        this.clock = clock;
    }

    @Transactional
    public AccountResponse handle(CurrentUser admin, UUID userId, ChangeUserStatusRequest request) {
        AccountStatus to = AccountStatus.valueOf(request.status());
        StatusReason reason = request.reasonCode() == null ? null : StatusReason.valueOf(request.reasonCode());
        if (to == AccountStatus.SUSPENDED && reason == null) {
            throw new ApiException(ErrorCode.USER_STATUS_REASON_REQUIRED);
        }
        if (admin.userId().equals(userId)) {
            throw new ApiException(ErrorCode.USER_STATUS_OWN_ACCOUNT);
        }

        AccountUser user = users.findById(userId).orElseThrow(() -> new ApiException(ErrorCode.USER_NOT_FOUND));
        AccountStatus from = user.getStatus();
        if (from == to) {
            throw new ApiException(ErrorCode.USER_STATUS_UNCHANGED);
        }

        Instant now = clock.instant();
        long version = user.changeStatus(to, now);
        String note = request.note() == null || request.note().isBlank() ? null : request.note().trim();
        history.save(AccountUserStatusChange.record(
                userId, version, from, to, reason, note, admin.keycloakUserId(), now));

        String keycloakId = userId.toString();
        keycloak.setEnabled(keycloakId, to == AccountStatus.ACTIVE);
        if (to == AccountStatus.SUSPENDED) {
            keycloak.logoutAllSessions(keycloakId);
        }
        log.info("User status {} -> {} for user_id={} by admin user_id={} (reason={})",
                from, to, userId, admin.userId(), reason);
        return AccountMapper.toResponse(user);
    }
}
