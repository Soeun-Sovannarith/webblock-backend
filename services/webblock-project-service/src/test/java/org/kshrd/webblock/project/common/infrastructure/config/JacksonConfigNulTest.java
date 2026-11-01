package org.kshrd.webblock.project.common.infrastructure.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.jackson.autoconfigure.JacksonAutoConfiguration;
import org.springframework.boot.jackson.autoconfigure.JsonFactoryBuilderCustomizer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import tools.jackson.core.JacksonException;
import tools.jackson.core.JsonParser;
import tools.jackson.core.StreamReadConstraints;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * The application's own JsonMapper (Boot's auto-configuration plus {@link JacksonConfig}) refuses U+0000 and unpaired
 * UTF-16 surrogates, which PostgreSQL cannot store as text.
 */
class JacksonConfigNulTest {

    /** The six characters backslash, u, 0, 0, 0, 0: a JSON escape for NUL. */
    private static final String NUL = "\\" + "u0000";

    record Named(String name, Map<String, Object> extra) {}

    private void withMapper(java.util.function.Consumer<JsonMapper> test) {
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(JacksonAutoConfiguration.class))
                .withUserConfiguration(JacksonConfig.class)
                .run(context -> test.accept(context.getBean(JsonMapper.class)));
    }

    private static void refused(JsonMapper mapper, String json, Class<?> type) {
        assertThatThrownBy(() -> mapper.readValue(json, type))
                .isInstanceOf(JacksonException.class)
                .hasMessageContaining("NUL")
                .hasMessageNotContaining("secret");
    }

    @Test
    void aNulInAValueIsRefused() {
        withMapper(m -> refused(m, "{\"name\":\"secret" + NUL + "x\"}", Named.class));
    }

    @Test
    void aNulInAKeyIsRefusedForAMapAndForATree() {
        withMapper(m -> {
            refused(m, "{\"name\":\"a\",\"extra\":{\"secret" + NUL + "k\":1}}", Named.class);
            refused(m, "{\"secret" + NUL + "k\":1}", JsonNode.class);
            refused(m, "{\"secret" + NUL + "k\":1}", Map.class);
        });
    }

    @Test
    void aNulNestedInAnArrayOrObjectIsRefusedWhenReadAsATreeNodeOrAMap() {
        withMapper(m -> {
            String tree = "{\"root\":{\"props\":{}},\"content\":[{\"type\":\"Text\",\"props\":{\"t\":[\"ok\",\"secret"
                    + NUL + "\"]}}]}";
            assertThatThrownBy(() -> m.readTree(tree))
                    .isInstanceOf(JacksonException.class)
                    .hasMessageContaining("NUL");
            refused(m, tree, JsonNode.class);
            refused(m, tree, Map.class);
            refused(m, tree, Object.class);
        });
    }

    @Test
    void aNulReadFromBytesAndFromAStreamIsRefused() {
        withMapper(m -> {
            byte[] bytes = ("{\"name\":\"secret" + NUL + "\"}").getBytes(java.nio.charset.StandardCharsets.UTF_8);
            assertThatThrownBy(() -> m.readValue(bytes, Named.class))
                    .isInstanceOf(JacksonException.class)
                    .hasMessageContaining("NUL");
            assertThatThrownBy(() -> m.readValue(new java.io.ByteArrayInputStream(bytes), Named.class))
                    .isInstanceOf(JacksonException.class)
                    .hasMessageContaining("NUL");
        });
    }

    @Test
    void ordinaryTextAndAnEscapedBackslashBeforeU0000AreAccepted() {
        withMapper(m -> {
            Named plain = m.readValue("{\"name\":\"Caf\u00e9 \\u00e9 \\n\"}", Named.class);
            assertThat(plain.name()).isEqualTo("Caf\u00e9 \u00e9 \n");
            // JSON "\\u0000" is a backslash followed by the literal text u0000, not a NUL.
            Named literal = m.readValue("{\"name\":\"a\\\\u0000b\"}", Named.class);
            assertThat(literal.name()).isEqualTo("a\\u0000b").doesNotContain("\0");
            assertThat(m.readTree("{\"k\\\\u0000\":[\"v\\\\u0000\"]}").toString())
                    .contains("u0000");
        });
    }

    /** JSON escapes for a lone high surrogate, a lone low one, and a pair in the wrong order. */
    @Test
    void anUnpairedSurrogateInAValueOrAKeyIsRefused() {
        withMapper(m -> {
            for (String lone : new String[] {"\\ud800", "\\udc00", "\\udc00\\ud800", "x\\ud83d"}) {
                assertThatThrownBy(() -> m.readValue("{\"name\":\"secret" + lone + "\"}", Named.class))
                        .as(lone)
                        .isInstanceOf(JacksonException.class)
                        .hasMessageContaining("surrogate")
                        .hasMessageNotContaining("secret");
                assertThatThrownBy(() -> m.readTree("{\"k" + lone + "\":[1]}"))
                        .as(lone)
                        .hasMessageContaining("surrogate");
                assertThatThrownBy(() -> m.readTree("{\"a\":{\"b\":[\"ok\",\"" + lone + "\"]}}"))
                        .as(lone)
                        .hasMessageContaining("surrogate");
            }
        });
    }

    @Test
    void aSurrogatePairIsAccepted() {
        withMapper(m -> {
            String smile = new String(Character.toChars(0x1F600));
            assertThat(m.readValue("{\"name\":\"\\ud83d\\ude00 shop\"}", Named.class)
                            .name())
                    .isEqualTo(smile + " shop");
            byte[] utf8 = ("{\"name\":\"" + smile + "\"}").getBytes(java.nio.charset.StandardCharsets.UTF_8);
            assertThat(m.readValue(utf8, Named.class).name()).isEqualTo(smile);
        });
    }

    @Test
    void nextValueChecksThePropertyNameToo() {
        withMapper(m -> {
            try (JsonParser parser = m.createParser("{\"k" + NUL + "\":1}")) {
                parser.nextToken();
                assertThatThrownBy(parser::nextValue).hasMessageContaining("NUL");
            }
        });
    }

    @Test
    void aBootFactoryCustomizerStillReachesTheFactory() {
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(JacksonAutoConfiguration.class))
                .withUserConfiguration(JacksonConfig.class)
                .withBean(
                        JsonFactoryBuilderCustomizer.class,
                        () -> builder -> builder.streamReadConstraints(StreamReadConstraints.builder()
                                .maxStringLength(7)
                                .build()))
                .run(context -> {
                    JsonMapper mapper = context.getBean(JsonMapper.class);
                    assertThat(mapper.tokenStreamFactory()
                                    .streamReadConstraints()
                                    .getMaxStringLength())
                            .isEqualTo(7);
                    assertThatThrownBy(() -> mapper.readValue("{\"name\":\"longer than seven\"}", Named.class))
                            .isInstanceOf(JacksonException.class);
                });
    }
}
