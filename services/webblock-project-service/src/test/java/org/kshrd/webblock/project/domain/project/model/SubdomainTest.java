package org.kshrd.webblock.project.domain.project.model;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class SubdomainTest {

    private static final List<String> RESERVED = List.of("www", "auth");

    @ParameterizedTest
    @ValueSource(strings = {"kilncoffee", "kiln2", "abc", "a-b", "p-0192f3a4-5b6c", "0shop9"})
    @DisplayName("FR-13.3: one DNS label of 3 to 55 lowercase letters, digits and dashes is allowed")
    void fr13_3_aLabelIsAllowed(String value) {
        assertThat(Subdomain.isLabel(value)).isTrue();
        assertThat(Subdomain.isAllowed(value, RESERVED)).isTrue();
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "",
                "ab",
                "-shop",
                "shop-",
                "Kiln",
                "kiln coffee",
                "kiln_coffee",
                "kiln.coffee",
                "kilncafé",
                "x123456789x123456789x123456789x123456789x123456789x123456789xxxx",
                "kilncoffee-preview",
                "xn--kiln-9ra"
            })
    @DisplayName("FR-13.3: anything but one DNS label is refused")
    void fr13_3_notALabelIsRefused(String value) {
        assertThat(Subdomain.isLabel(value)).isFalse();
        assertThat(Subdomain.isAllowed(value, List.of())).isFalse();
    }

    @ParameterizedTest
    @ValueSource(strings = {"www", "auth"})
    @DisplayName("FR-13.3: a reserved name is refused")
    void fr13_3_aReservedNameIsRefused(String value) {
        assertThat(Subdomain.isLabel(value)).isTrue();
        assertThat(Subdomain.isAllowed(value, RESERVED)).isFalse();
    }

    @DisplayName("FR-13.3: no value is refused")
    @Test
    void fr13_3_noValueIsRefused() {
        assertThat(Subdomain.isLabel(null)).isFalse();
        assertThat(Subdomain.isAllowed(null, RESERVED)).isFalse();
    }

    @DisplayName("FR-13.11: the longest label is 55 characters; 56 is refused")
    @Test
    void fr13_11_fiftyFiveCharactersAreAllowed() {
        assertThat(Subdomain.isAllowed("a".repeat(55), RESERVED)).isTrue();
        assertThat(Subdomain.isAllowed("a".repeat(56), RESERVED)).isFalse();
        assertThat(Subdomain.isAllowed("a".repeat(63), RESERVED)).isFalse();
    }

    @ParameterizedTest
    @ValueSource(strings = {"kilncoffee-preview", "a-preview", "xn--kiln-9ra", "xn--abc"})
    @DisplayName("FR-13.5: a label ending with -preview or starting with xn-- is refused")
    void fr13_5_previewSuffixAndPunycodePrefixAreRefused(String value) {
        assertThat(Subdomain.isLabel(value)).isFalse();
        assertThat(Subdomain.isAllowed(value, List.of())).isFalse();
    }

    @ParameterizedTest
    @ValueSource(strings = {"previewshop", "kiln-previews", "xnshop", "xn-shop", "preview-kiln", "p-0192f3a4-5b6c"})
    @DisplayName("FR-13.8: a label that only resembles the preview or punycode forms is allowed")
    void fr13_8_lookalikesAreAllowed(String value) {
        assertThat(Subdomain.isAllowed(value, RESERVED)).isTrue();
    }

    @DisplayName("FR-13.8: a 55-character label that is not a preview label is allowed")
    @Test
    void fr13_8_aFiftyFiveCharacterLabelIsAllowed() {
        assertThat(Subdomain.isAllowed("kiln" + "a".repeat(51), RESERVED)).isTrue();
    }

    @DisplayName("FR-13.6: preview is refused when the list reserves it")
    @Test
    void fr13_6_previewIsReserved() {
        assertThat(Subdomain.isLabel("preview")).isTrue();
        assertThat(Subdomain.isAllowed("preview", List.of("preview"))).isFalse();
    }
}
