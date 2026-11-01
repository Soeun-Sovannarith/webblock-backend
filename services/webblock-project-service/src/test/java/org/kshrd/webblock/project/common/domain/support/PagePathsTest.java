package org.kshrd.webblock.project.common.domain.support;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** The rule of the database constraint pages_path_check plus the column length, without Bean Validation. */
class PagePathsTest {

    @ParameterizedTest
    @ValueSource(strings = {"/", "/products", "/a/b-c/d1"})
    void acceptsValidPaths(String path) {
        assertThat(PagePaths.isValid(path)).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "products", "/Products", "/a/", "//", "/a b"})
    void rejectsInvalidPaths(String path) {
        assertThat(PagePaths.isValid(path)).isFalse();
    }

    @Test
    void nullIsNotAPath() {
        assertThat(PagePaths.isValid(null)).isFalse();
    }

    @Test
    void limitsTheLengthToTheColumn() {
        assertThat(PagePaths.MAX_LENGTH).isEqualTo(255);
        assertThat(PagePaths.isValid("/" + "a".repeat(PagePaths.MAX_LENGTH - 1)))
                .isTrue();
        assertThat(PagePaths.isValid("/" + "a".repeat(PagePaths.MAX_LENGTH))).isFalse();
    }

    /**
     * The pattern recurses once per segment, so matching it alone against about 20 KB of segments overflows the stack.
     * Only the length-checked isValid is exposed.
     */
    @Test
    void onlyTheLengthCheckedRuleIsExposed() {
        assertThat(Arrays.stream(PagePaths.class.getDeclaredFields())
                        .filter(f -> Pattern.class.equals(f.getType()))
                        .allMatch(f -> Modifier.isPrivate(f.getModifiers())))
                .isTrue();
        assertThat(PagePaths.isValid("/a".repeat(20_000))).isFalse();
    }
}
