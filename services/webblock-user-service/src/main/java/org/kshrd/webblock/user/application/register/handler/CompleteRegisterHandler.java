package org.kshrd.webblock.user.application.register.handler;

import org.kshrd.webblock.user.application.otp.OtpManager;
import org.kshrd.webblock.user.application.register.request.CompleteRegisterRequest;
import org.kshrd.webblock.user.application.register.response.RegisteredResponse;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.account.model.AccountUser;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.kshrd.webblock.user.infrastructure.client.keycloak.KeycloakAdminClient;
import org.kshrd.webblock.user.infrastructure.persistence.account.AccountUserRepository;
import org.kshrd.webblock.user.infrastructure.persistence.otp.OtpStore;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.util.Optional;
import java.util.UUID;

// Register step 3: create the account in Keycloak (or add a password to a Google-only account),
// then save the account_users row. user_id = the Keycloak user id (team decision).
@Component
public class CompleteRegisterHandler {

    private final OtpManager otp;
    private final KeycloakAdminClient keycloak;
    private final AccountUserRepository accounts;
    private final Clock clock;
    private final String issuer;

    public CompleteRegisterHandler(OtpManager otp, KeycloakAdminClient keycloak, AccountUserRepository accounts,
                                   Clock clock,
                                   @Value("${spring.security.oauth2.resourceserver.jwt.issuer-uri}") String issuer) {
        this.otp = otp;
        this.keycloak = keycloak;
        this.accounts = accounts;
        this.clock = clock;
        this.issuer = issuer;
    }

    @Transactional
    public RegisteredResponse handle(CompleteRegisterRequest request) {
        // Check what we can BEFORE using up the one-time token, so a typo doesn't force a new code
        if (!request.password().equals(request.confirmPassword())) {
            throw new ApiException(ErrorCode.PASSWORD_MISMATCH);
        }

        OtpStore.Verification verified = otp.takeVerification(OtpPurpose.REGISTER, request.verificationToken());
        String email = verified.email();
        if (request.password().equalsIgnoreCase(email)) {
            throw new ApiException(ErrorCode.PASSWORD_TOO_WEAK); // realm policy: not the username (= email)
        }
        String fullName = verified.fullName().isBlank() ? email : verified.fullName();

        Optional<KeycloakAdminClient.KeycloakUser> existing = keycloak.findUserByEmail(email);
        String keycloakId;
        if (existing.isPresent()) {
            keycloakId = existing.get().id();
            if (keycloak.hasPassword(keycloakId)) {
                throw new ApiException(ErrorCode.ACCOUNT_ALREADY_EXISTS);
            }
            keycloak.setPassword(keycloakId, request.password()); // Google-only account gets a password
        } else {
            keycloakId = keycloak.createUser(email, fullName, request.password());
        }

        UUID userId = UUID.fromString(keycloakId);
        if (accounts.findById(userId).isEmpty()) {
            accounts.save(AccountUser.create(userId, issuer, keycloakId, email, fullName, clock.instant()));
        }
        return new RegisteredResponse(userId, email);
    }
}
