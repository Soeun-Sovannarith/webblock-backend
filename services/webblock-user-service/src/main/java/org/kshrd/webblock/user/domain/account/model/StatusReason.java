package org.kshrd.webblock.user.domain.account.model;

// Exact values from docs/specs/status-flows.md (account_user_status_changes.reason_code)
public enum StatusReason {
    ABUSE,
    FRAUD,
    OWNER_REQUEST,
    OTHER
}
