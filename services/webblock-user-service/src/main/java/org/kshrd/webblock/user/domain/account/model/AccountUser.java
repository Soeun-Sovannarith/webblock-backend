package org.kshrd.webblock.user.domain.account.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

// One row of account_users (columns = docs/specs/data-dictionary.md).
// Passwords, roles and the profile photo live in Keycloak, not here.
@Entity
@Table(name = "account_users")
public class AccountUser {

    @Id
    @Column(name = "user_id", nullable = false, updatable = false)
    private UUID userId;

    @Column(name = "identity_issuer", nullable = false, updatable = false, length = 255)
    private String identityIssuer;      // token "iss"

    @Column(name = "identity_subject", nullable = false, updatable = false, length = 255)
    private String identitySubject;     // token "sub" (Keycloak user id)

    @Column(name = "email", nullable = false, length = 320)
    private String email;

    @Column(name = "display_name", length = 255)
    private String displayName;         // full name

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private AccountStatus status;

    @Column(name = "status_version", nullable = false)
    private long statusVersion;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "last_login_at")
    private Instant lastLoginAt;

    protected AccountUser() {
        // for JPA
    }

    // A new account always starts ACTIVE with status_version 0.
    // userId = the token's "user_id" (team decision: the same value as the Keycloak user id).
    public static AccountUser create(UUID userId, String issuer, String subject, String email, String displayName,
                                     Instant now) {
        AccountUser user = new AccountUser();
        user.userId = userId;
        user.identityIssuer = issuer;
        user.identitySubject = subject;
        user.email = email;
        user.displayName = displayName;
        user.status = AccountStatus.ACTIVE;
        user.statusVersion = 0;
        user.createdAt = now;
        user.updatedAt = now;
        return user;
    }

    public void changeDisplayName(String newDisplayName, Instant now) {
        this.displayName = newDisplayName;
        this.updatedAt = now;
    }

    // Keep the email in sync with Keycloak (the token is the source of truth for it).
    public void syncEmail(String emailFromToken, Instant now) {
        if (emailFromToken != null && !emailFromToken.equalsIgnoreCase(this.email)) {
            this.email = emailFromToken;
            this.updatedAt = now;
        }
    }

    // Only move forward: an older sign-in time never replaces a newer one.
    public void recordSignIn(Instant signedInAt) {
        if (signedInAt != null && (lastLoginAt == null || signedInAt.isAfter(lastLoginAt))) {
            this.lastLoginAt = signedInAt;
        }
    }

    // Admin suspend / activate. status_version goes up by 1; the caller writes one
    // account_user_status_changes row with this new version. Returns the new version.
    public long changeStatus(AccountStatus newStatus, Instant now) {
        this.status = newStatus;
        this.statusVersion = this.statusVersion + 1;
        this.updatedAt = now;
        return this.statusVersion;
    }

    public boolean isSuspended() {
        return status == AccountStatus.SUSPENDED;
    }

    public UUID getUserId() { return userId; }
    public String getIdentityIssuer() { return identityIssuer; }
    public String getIdentitySubject() { return identitySubject; }
    public String getEmail() { return email; }
    public String getDisplayName() { return displayName; }
    public AccountStatus getStatus() { return status; }
    public long getStatusVersion() { return statusVersion; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getLastLoginAt() { return lastLoginAt; }
}
