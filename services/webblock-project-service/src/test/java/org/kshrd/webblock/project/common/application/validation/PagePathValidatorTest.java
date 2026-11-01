package org.kshrd.webblock.project.common.application.validation;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class PagePathValidatorTest {

    private final PagePathValidator validator = new PagePathValidator();

    @ParameterizedTest
    @ValueSource(strings = {"/", "/products", "/about-us", "/a/b-c/d1", "/2026"})
    void acceptsValidPaths(String path) {
        assertThat(validator.isValid(path, null)).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "products", "/Products", "/a/", "//", "/a//b", "/a b", "/ä", "/a_b", "/a?b", " /a"})
    void rejectsInvalidPaths(String path) {
        assertThat(validator.isValid(path, null)).isFalse();
    }

    @Test
    void leavesNullToNotNull() {
        assertThat(validator.isValid(null, null)).isTrue();
    }

    @Test
    void limitsTheLengthTo255() {
        assertThat(validator.isValid("/" + "a".repeat(254), null)).isTrue();
        assertThat(validator.isValid("/" + "a".repeat(255), null)).isFalse();
    }
}
