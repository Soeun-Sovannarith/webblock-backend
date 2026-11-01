package org.kshrd.webblock.project.application.generation.handler;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.HexFormat;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

class InputHasherTest {

    private static String read(String name) throws IOException {
        return read("/jcs/rfc8785/", name);
    }

    private static String read(String dir, String name) throws IOException {
        try (InputStream in = InputHasherTest.class.getResourceAsStream(dir + name)) {
            return new String(in.readAllBytes(), UTF_8);
        }
    }

    private static String canonical(String json) {
        return new String(InputHasher.canonicalize(json), UTF_8);
    }

    @ParameterizedTest
    @ValueSource(strings = {"sorting", "values"})
    void rfc8785ExamplesCanonicalizeExactly(String name) throws IOException {
        assertThat(canonical(read(name + ".input.json"))).isEqualTo(read(name + ".expected.json"));
    }

    /** Project-authored fixture, not an RFC 8785 example (see src/test/resources/jcs/project). */
    @Test
    void whitespaceIsDropped() throws IOException {
        assertThat(canonical(read("/jcs/project/", "whitespace.input.json")))
                .isEqualTo(read("/jcs/project/", "whitespace.expected.json"));
    }

    /** Every value row of RFC 8785 Appendix B: IEEE-754 bits to the exact JCS text. */
    @ParameterizedTest
    @CsvSource({
        "0000000000000000, 0",
        "8000000000000000, 0",
        "0000000000000001, 5e-324",
        "8000000000000001, -5e-324",
        "7fefffffffffffff, 1.7976931348623157e+308",
        "ffefffffffffffff, -1.7976931348623157e+308",
        "4340000000000000, 9007199254740992",
        "c340000000000000, -9007199254740992",
        "4430000000000000, 295147905179352830000",
        "44b52d02c7e14af5, 9.999999999999997e+22",
        "44b52d02c7e14af6, 1e+23",
        "44b52d02c7e14af7, 1.0000000000000001e+23",
        "444b1ae4d6e2ef4e, 999999999999999700000",
        "444b1ae4d6e2ef4f, 999999999999999900000",
        "444b1ae4d6e2ef50, 1e+21",
        "3eb0c6f7a0b5ed8c, 9.999999999999997e-7",
        "3eb0c6f7a0b5ed8d, 0.000001",
        "41b3de4355555553, 333333333.3333332",
        "41b3de4355555554, 333333333.33333325",
        "41b3de4355555555, 333333333.3333333",
        "41b3de4355555556, 333333333.3333334",
        "41b3de4355555557, 333333333.33333343",
        "becbf647612f3696, -0.0000033333333333333333",
        "43143ff3c1cb0959, 1424953923781206.2"
    })
    void appendixBTableByIeeeBits(String hexBits, String expected) {
        double d = Double.longBitsToDouble(Long.parseUnsignedLong(hexBits, 16));
        assertThat(canonical("[" + Double.toString(d) + "]")).isEqualTo("[" + expected + "]");
    }

    @ParameterizedTest
    @ValueSource(strings = {"[1e999]", "[-1e999]"})
    void nonFiniteNumbersAreRefused(String json) {
        assertThatThrownBy(() -> InputHasher.canonicalize(json)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void errorMessageDoesNotEchoTheInput() {
        assertThatThrownBy(() -> InputHasher.canonicalize("{\"secretValue\": nope}"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("not valid JSON")
                .hasNoCause()
                .message()
                .doesNotContain("secretValue")
                .doesNotContain("nope");
    }

    @Test
    void deepNestingIsRefusedNotAStackOverflow() {
        String json = "[".repeat(100_000);
        assertThatThrownBy(() -> InputHasher.canonicalize(json))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("not valid JSON")
                .hasNoCause();
    }

    @ParameterizedTest
    @CsvSource({
        "1e21, 1e+21",
        "-5e-324, -5e-324",
        "-9007199254740992, -9007199254740992",
        "999999999999999700000, 999999999999999700000",
        "999999999999999900000, 999999999999999900000",
        "5e-324, 5e-324",
        "9007199254740993, 9007199254740992",
        "0.000001, 0.000001",
        "1e-7, 1e-7",
        "-0, 0",
        "1.0, 1",
        "1e2, 100",
        "0.0, 0",
        "1.7976931348623157e308, 1.7976931348623157e+308",
        "295147905179352830000, 295147905179352830000",
        "9.999999999999997e22, 9.999999999999997e+22",
        "1e23, 1e+23",
        "-0.0000033333333333333335, -0.0000033333333333333333",
        "333333333.33333329, 333333333.3333333",
        "1424953923781206.2, 1424953923781206.2",
        "-1e-7, -1e-7"
    })
    void numberSamplesFollowAppendixB(String input, String expected) {
        assertThat(canonical("[" + input + "]")).isEqualTo("[" + expected + "]");
    }

    @Test
    void numberAndStringFormsCanonicalizeAsRfc8785() {
        assertThat(canonical("{\"b\":1.0,\"a\":[1e2,-0,0.000001,1E-7],\"é\":\"\\u00e9\",\"e\":\"\\/\"}"))
                .isEqualTo("{\"a\":[100,0,0.000001,1e-7],\"b\":1,\"e\":\"/\",\"é\":\"é\"}");
    }

    @Test
    void sha256IsLowercaseHexOfTheCanonicalBytes() throws Exception {
        String a = "{\"b\":2,\"a\":1}";
        String b = "{ \"a\" : 1, \"b\" : 2 }";
        assertThat(InputHasher.sha256(a)).isEqualTo(InputHasher.sha256(b)).matches("^[0-9a-f]{64}$");
        assertThat(InputHasher.sha256(a))
                .isEqualTo(HexFormat.of()
                        .formatHex(MessageDigest.getInstance("SHA-256").digest("{\"a\":1,\"b\":2}".getBytes(UTF_8))));
    }

    @ParameterizedTest
    @ValueSource(strings = {"{", "", "[1,", "{\"a\":}", "not json", "{\"a\":1} x", "\"\\u12\""})
    void invalidJsonIsRefused(String json) {
        assertThatThrownBy(() -> InputHasher.canonicalize(json)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> InputHasher.sha256(json)).isInstanceOf(IllegalArgumentException.class);
    }
}
