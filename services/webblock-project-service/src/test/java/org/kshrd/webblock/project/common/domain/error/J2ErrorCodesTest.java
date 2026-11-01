package org.kshrd.webblock.project.common.domain.error;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;

class J2ErrorCodesTest {

    private static final Map<String, ErrorCategory> EXPECTED = Map.of(
            "GENERATION_CONTRACT_NOT_FOUND", ErrorCategory.NOT_FOUND,
            "GENERATION_REQUEST_NOT_FOUND", ErrorCategory.NOT_FOUND,
            "PRODUCT_TYPE_NOT_ALLOWED", ErrorCategory.BUSINESS_RULE,
            "IDEMPOTENCY_KEY_REUSED", ErrorCategory.CONFLICT,
            "GENERATION_IN_PROGRESS", ErrorCategory.CONFLICT,
            "REQUEST_NOT_GENERATING", ErrorCategory.CONFLICT);

    @Test
    void everyJ2CodeExistsWithItsCategoryAndAFixedSentence() {
        EXPECTED.forEach((name, category) -> {
            ErrorCode code = ErrorCode.fromCode(name).orElseThrow(() -> new AssertionError("missing " + name));
            assertThat(code.category()).as(name).isEqualTo(category);
            assertThat(code.message()).as(name).isNotBlank().endsWith(".").doesNotContain("%s", "{");
        });
    }
}
