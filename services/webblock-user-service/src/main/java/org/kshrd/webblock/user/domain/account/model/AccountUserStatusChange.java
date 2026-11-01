package org.kshrd.webblock.user.domain.account.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

// One row of account_user_status_changes: the history of suspend / unsuspend (who, when, why).
// Rows are only added, never changed.
@Entity
@Table(name = "account_user_status_changes")
public class AccountUserStatusChange {

    @Id
    @Column(name = "user_status_change_id", nullable = false, updatable = false)
    private UUID id;

    @Column(name = "user_id", nullable = false, updatable = false)
    private UUID userId;

    @Column(name = "status_version", nullable = false, updatable = false)
    private long statusVersion;

    @Enumerated(EnumType.STRING)
    @Column(name = "from_status", nullable = false, updatable = false, length = 20)
    private AccountStatus fromStatus;

    @Enumerated(EnumType.STRING)
    @Column(name = "to_status", nullable = false, updatable = false, length = 20)
    private AccountStatus toStatus;

    @Enumerated(EnumType.STRING)
    @Column(name = "reason_code", updatable = false, length = 20)
    private StatusReason reasonCode;

    @Column(name = "note", updatable = false, length = 500)
    private String note;                // private admin note

    @Column(name = "changed_by", updatable = false, length = 255)
    private String changedBy;           // admin's Keycloak id; null for a system change

    @Column(name = "changed_at", nullable = false, updatable = false)
    private Instant changedAt;

    protected AccountUserStatusChange() {
        // for JPA
    }

    public static AccountUserStatusChange record(UUID userId, long statusVersion, AccountStatus from, AccountStatus to,
                                                 StatusReason reason, String note, String changedBy, Instant now) {
        AccountUserStatusChange change = new AccountUserStatusChange();
        change.id = UUID.randomUUID();
        change.userId = userId;
        change.statusVersion = statusVersion;
        change.fromStatus = from;
        change.toStatus = to;
        change.reasonCode = reason;
        change.note = note;
        change.changedBy = changedBy;
        change.changedAt = now;
        return change;
    }

    public UUID getId() { return id; }
    public UUID getUserId() { return userId; }
    public long getStatusVersion() { return statusVersion; }
    public AccountStatus getFromStatus() { return fromStatus; }
    public AccountStatus getToStatus() { return toStatus; }
    public StatusReason getReasonCode() { return reasonCode; }
    public String getNote() { return note; }
    public String getChangedBy() { return changedBy; }
    public Instant getChangedAt() { return changedAt; }
}
