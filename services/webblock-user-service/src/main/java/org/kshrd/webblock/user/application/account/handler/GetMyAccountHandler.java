package org.kshrd.webblock.user.application.account.handler;

import org.kshrd.webblock.user.application.account.mapper.AccountMapper;
import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.account.model.AccountUser;
import org.kshrd.webblock.user.infrastructure.persistence.account.AccountUserRepository;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;

// GET /me: return the caller's account. The first time someone calls it
// (new email user, or a new Google user), the account_users row is created.
@Component
public class GetMyAccountHandler {

    private final AccountUserRepository repository;
    private final Clock clock;

    public GetMyAccountHandler(AccountUserRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    @Transactional
    public AccountResponse handle(CurrentUser caller) {
        Instant now = clock.instant();

        AccountUser user = repository
                .findById(caller.userId())
                .orElseGet(() -> createFirstTime(caller, now));

        if (user.isSuspended()) {
            throw new ApiException(ErrorCode.ACCOUNT_SUSPENDED);
        }

        user.syncEmail(caller.email(), now);
        user.recordSignIn(caller.signedInAt());
        return AccountMapper.toResponse(user);
    }

    private AccountUser createFirstTime(CurrentUser caller, Instant now) {
        if (caller.email() == null || caller.email().isBlank()) {
            throw new ApiException(ErrorCode.ACCOUNT_EMAIL_MISSING);
        }
        return repository.save(AccountUser.create(
                caller.userId(), caller.issuer(), caller.subject(), caller.email(), caller.fullName(), now));
    }
}
