package org.kshrd.webblock.project.application.generation.handler;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;

/**
 * Keyset position in a project's generation requests: the last row's created_at (microseconds) and ID, base64url. A
 * copy of the project feature's ProjectCursor, which is package-private there (lead ruling C10).
 */
record RequestCursor(Instant createdAt, UUID generationRequestId) {

    static final int MAX_LENGTH = 100;

    String encode() {
        String raw = ChronoUnit.MICROS.between(Instant.EPOCH, createdAt) + ":" + generationRequestId;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.US_ASCII));
    }

    static RequestCursor decode(String text) {
        try {
            if (text == null || text.isEmpty() || text.length() > MAX_LENGTH) {
                throw new IllegalArgumentException("length");
            }
            String raw = new String(Base64.getUrlDecoder().decode(text), StandardCharsets.US_ASCII);
            int colon = raw.indexOf(':');
            long micros = Long.parseLong(raw.substring(0, colon));
            String id = raw.substring(colon + 1);
            UUID generationRequestId = UUID.fromString(id);
            if (!generationRequestId.toString().equals(id)) {
                throw new IllegalArgumentException("uuid");
            }
            return new RequestCursor(Instant.EPOCH.plus(micros, ChronoUnit.MICROS), generationRequestId);
        } catch (RuntimeException e) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("cursor", ErrorCode.VALUE_INVALID)));
        }
    }
}
