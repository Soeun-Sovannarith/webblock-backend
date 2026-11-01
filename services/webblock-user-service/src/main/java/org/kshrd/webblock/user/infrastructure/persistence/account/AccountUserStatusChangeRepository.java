package org.kshrd.webblock.user.infrastructure.persistence.account;

import org.kshrd.webblock.user.domain.account.model.AccountUserStatusChange;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface AccountUserStatusChangeRepository extends JpaRepository<AccountUserStatusChange, UUID> {
}
