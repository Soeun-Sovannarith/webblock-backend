package org.kshrd.webblock.user.application.account.mapper;

import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.domain.account.model.AccountUser;

public final class AccountMapper {

    private AccountMapper() {
    }

    public static AccountResponse toResponse(AccountUser user) {
        return new AccountResponse(
                user.getUserId(),
                user.getEmail(),
                user.getDisplayName(),
                user.getStatus().name(),
                user.getCreatedAt(),
                user.getLastLoginAt());
    }
}
