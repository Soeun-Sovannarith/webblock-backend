package org.kshrd.webblock.project.common.application.validation;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class IdempotencyKeyValidatorTest {

    private final IdempotencyKeyValidator validator = new IdempotencyKeyValidator();

    @ParameterizedTest
    @ValueSource(strings = {"k", "publish-2026-10-13", "0190a7c2-7e5b-7c3e-9a1b-1234567890ab", "a b", "ключ"})
    void acceptsValidKeys(String key) {
        assertThat(validator.isValid(key, null)).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", " k", "k ", "\tk", "a\nb", "a\u0000b", "a\u007fb"})
    void rejectsInvalidKeys(String key) {
        assertThat(validator.isValid(key, null)).isFalse();
    }

    @Test
    void countsBytesNotCharacters() {
        assertThat(validator.isValid("a".repeat(255), null)).isTrue();
        assertThat(validator.isValid("a".repeat(256), null)).isFalse();
        assertThat(validator.isValid("é".repeat(127), null)).isTrue(); // 254 bytes
        assertThat(validator.isValid("é".repeat(128), null)).isFalse(); // 256 bytes
    }

    @Test
    void leavesNullToNotNull() {
        assertThat(validator.isValid(null, null)).isTrue();
    }
}
