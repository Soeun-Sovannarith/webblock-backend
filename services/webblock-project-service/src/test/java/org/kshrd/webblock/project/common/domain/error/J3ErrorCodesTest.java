package org.kshrd.webblock.project.common.domain.error;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class J3ErrorCodesTest {

    private static final Map<String, String> EXPECTED = Map.of(
            "PROJECT_DELETED", "This project is deleted.",
            "PROJECT_SUSPENDED", "This project is suspended.");

    @Test
    @DisplayName("FR-33.2: PROJECT_DELETED and PROJECT_SUSPENDED exist with status 409 and a fixed sentence")
    void fr33_2_bothCodesExistWithStatus409() {
        EXPECTED.forEach((name, sentence) -> {
            ErrorCode code = ErrorCode.fromCode(name).orElseThrow(() -> new AssertionError("missing " + name));
            assertThat(code.category()).as(name).isEqualTo(ErrorCategory.CONFLICT);
            assertThat(code.category().httpStatus()).as(name).isEqualTo(409);
            assertThat(code.message()).as(name).isEqualTo(sentence);
            assertThat(code.isFieldCode()).as(name).isFalse();
        });
    }
}
