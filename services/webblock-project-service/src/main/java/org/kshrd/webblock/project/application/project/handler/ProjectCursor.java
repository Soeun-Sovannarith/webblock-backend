package org.kshrd.webblock.project.application.project.handler;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;

/** Keyset position in "my projects": the last row's created_at (microseconds) and ID, base64url. */
record ProjectCursor(Instant createdAt, UUID projectId) {

    static final int MAX_LENGTH = 100;

    String encode() {
        String raw = ChronoUnit.MICROS.between(Instant.EPOCH, createdAt) + ":" + projectId;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.US_ASCII));
    }

    static ProjectCursor decode(String text) {
        try {
            if (text == null || text.isEmpty() || text.length() > MAX_LENGTH) {
                throw new IllegalArgumentException("length");
            }
            String raw = new String(Base64.getUrlDecoder().decode(text), StandardCharsets.US_ASCII);
            int colon = raw.indexOf(':');
            long micros = Long.parseLong(raw.substring(0, colon));
            String id = raw.substring(colon + 1);
            UUID projectId = UUID.fromString(id);
            if (!projectId.toString().equals(id)) {
                throw new IllegalArgumentException("uuid");
            }
            return new ProjectCursor(Instant.EPOCH.plus(micros, ChronoUnit.MICROS), projectId);
        } catch (RuntimeException e) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("cursor", ErrorCode.VALUE_INVALID)));
        }
    }
}
