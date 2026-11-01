package org.kshrd.webblock.user.infrastructure.persistence.account;

import org.kshrd.webblock.user.domain.account.model.AccountUser;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

import java.util.Optional;
import java.util.UUID;

// JpaSpecificationExecutor: the admin user list builds its search/status filter from small pieces
public interface AccountUserRepository extends JpaRepository<AccountUser, UUID>, JpaSpecificationExecutor<AccountUser> {

    // One Keycloak identity (iss + sub) = one account row
    Optional<AccountUser> findByIdentityIssuerAndIdentitySubject(String identityIssuer, String identitySubject);
}
