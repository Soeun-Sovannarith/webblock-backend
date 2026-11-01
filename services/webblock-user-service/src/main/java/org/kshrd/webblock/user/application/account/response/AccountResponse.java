package org.kshrd.webblock.user.application.account.response;

import java.time.Instant;
import java.util.UUID;

// What GET/PUT /me return. Never return the AccountUser entity itself.
public record AccountResponse(
        UUID userId,
        String email,
        String fullName,
        String status,
        Instant createdAt,
        Instant lastLoginAt) {
}
