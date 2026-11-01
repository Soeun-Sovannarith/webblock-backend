package org.kshrd.webblock.project.application.generation.handler;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;

/**
 * Keyset position in a project's contracts: the last row's version_number, base64url. The same rules as the project
 * feature's ProjectCursor, which is package-private there (lead ruling C10).
 */
record ContractCursor(int versionNumber) {

    static final int MAX_LENGTH = 100;

    String encode() {
        String raw = Integer.toString(versionNumber);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.US_ASCII));
    }

    static ContractCursor decode(String text) {
        try {
            if (text == null || text.isEmpty() || text.length() > MAX_LENGTH) {
                throw new IllegalArgumentException("length");
            }
            String raw = new String(Base64.getUrlDecoder().decode(text), StandardCharsets.US_ASCII);
            int versionNumber = Integer.parseInt(raw);
            if (versionNumber < 1 || !Integer.toString(versionNumber).equals(raw)) {
                throw new IllegalArgumentException("version");
            }
            return new ContractCursor(versionNumber);
        } catch (RuntimeException e) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("cursor", ErrorCode.VALUE_INVALID)));
        }
    }
}
