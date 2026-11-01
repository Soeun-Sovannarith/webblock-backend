package org.kshrd.webblock.project.application.page.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

class PuckValidatorTest {

    static final JsonMapper JSON = JsonMapper.builder().build();
    static final PuckValidator VALIDATOR;

    static {
        try {
            VALIDATOR = new PuckValidator(JSON);
        } catch (java.io.IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static JsonNode tree(String json) {
        return JSON.readTree(json);
    }

    private static List<String> fields(List<ErrorDetail> details) {
        return details.stream().map(ErrorDetail::field).toList();
    }

    @Test
    void bothTheBundledAndTheTestCatalogsAreLoaded() {
        assertThat(VALIDATOR.knows("0.1.0")).isTrue();
        assertThat(VALIDATOR.knows("test-1")).isTrue();
        assertThat(VALIDATOR.knows("9.9.9")).isFalse();
    }

    @Test
    void aValidTreePasses() {
        assertThat(VALIDATOR.validate("test-1", tree("""
                {"root":{"props":{}},"content":[{"type":"Heading","props":{"id":"h1","text":"Hi"}}]}"""))).isEmpty();
    }

    @Test
    void aMissingPropIsReportedAtItsJsonPointer() {
        List<ErrorDetail> details = VALIDATOR.validate("test-1", tree("""
                {"root":{"props":{}},"content":[{"type":"Heading","props":{"id":"h1"}}]}"""));
        assertThat(fields(details)).containsExactly("puckTree/content/0/props/text");
        assertThat(details.getFirst().code()).isEqualTo("PUCK_TREE_INVALID");
    }

    @Test
    void anUnknownComponentTypeIsRejected() {
        assertThat(fields(VALIDATOR.validate("test-1", tree("""
                {"root":{"props":{}},"content":[{"type":"Video","props":{"id":"v"}}]}""")))).contains("puckTree/content/0/type");
    }

    @Test
    void anExtraTopLevelKeyIsRejected() {
        assertThat(fields(VALIDATOR.validate("0.1.0", tree("""
                {"root":{"props":{}},"content":[],"extra":1}""")))).containsExactly("puckTree");
    }

    @Test
    void theStructuralCatalogAcceptsAnEmptyPageAndRefusesANonObject() {
        assertThat(VALIDATOR.validate("0.1.0", tree("{\"root\":{\"props\":{}},\"content\":[]}")))
                .isEmpty();
        assertThat(fields(VALIDATOR.validate("0.1.0", tree("[]")))).containsExactly("puckTree");
    }

    @Test
    void manyErrorsAreCappedAtFifty() {
        ObjectNode root = JSON.createObjectNode();
        root.putObject("root").putObject("props");
        ArrayNode content = root.putArray("content");
        for (int i = 0; i < 60; i++) {
            content.addObject().put("type", "Heading").putObject("props").put("id", "h" + i);
        }
        assertThat(VALIDATOR.validate("test-1", root)).hasSize(PuckValidator.MAX_DETAILS);
    }

    @Test
    void anUnknownVersionIsAServerBug() {
        assertThatThrownBy(() -> VALIDATOR.validate("9.9.9", tree("{}"))).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void errorDetailsNeverQuoteTheContent() {
        List<ErrorDetail> details = VALIDATOR.validate("test-1", tree("""
                {"root":{"props":{}},"content":[{"type":"Heading","props":{"id":"h1","text":"%s"}}]}""".formatted("secret-".repeat(40))));
        assertThat(details)
                .isNotEmpty()
                .allSatisfy(d -> assertThat(d.toString()).doesNotContain("secret-"));
    }

    @Test
    void deeplyNestedPropsAreNotAStackOverflow() {
        ObjectNode props = JSON.createObjectNode().put("id", "x");
        ObjectNode cursor = props;
        for (int i = 0; i < 2000; i++) {
            cursor = cursor.putObject("n");
        }
        ObjectNode root = JSON.createObjectNode();
        root.putObject("root").putObject("props");
        root.putArray("content").addObject().put("type", "Anything").set("props", props);
        assertThat(VALIDATOR.validate("0.1.0", root)).isEmpty();
    }

    @Test
    void tooManyBlocksAndBadZoneNamesAreReportedNotThrown() {
        ObjectNode root = JSON.createObjectNode();
        root.putObject("root").putObject("props");
        ArrayNode content = root.putArray("content");
        for (int i = 0; i < 201; i++) {
            content.addObject().put("type", "Text").putObject("props").put("id", "t" + i);
        }
        root.putObject("zones").putArray("bad zone name");
        assertThat(fields(VALIDATOR.validate("0.1.0", root))).contains("puckTree/content", "puckTree/zones");
    }

    @Test
    void clientKeyNamesNeverAppearInTheOutput() {
        String key = "distinctive-key-".repeat(30);
        ObjectNode root = JSON.createObjectNode();
        root.putObject("root").putObject("props");
        root.put(key, 1);
        ObjectNode block = root.putArray("content").addObject().put("type", "Heading");
        block.putObject("props").put("id", "h1").put("text", "t").put(key, 2);
        block.put(key + "2", 3);
        List<ErrorDetail> details = VALIDATOR.validate("test-1", root);
        assertThat(fields(details))
                .containsExactlyInAnyOrder("puckTree", "puckTree/content/0", "puckTree/content/0/props");
        assertThat(details.toString()).doesNotContain("distinctive-key-");
    }

    @Test
    void aLongZoneNameIsNotEchoedInFull() {
        ObjectNode root = JSON.createObjectNode();
        root.putObject("root").putObject("props");
        root.putArray("content");
        root.putObject("zones").set("z".repeat(5000), JSON.createObjectNode());
        assertThat(VALIDATOR.validate("0.1.0", root))
                .isNotEmpty()
                .allSatisfy(d -> assertThat(d.field().length()).isLessThanOrEqualTo(PuckValidator.MAX_FIELD_LENGTH));
    }

    @Test
    void aJavaNullTreeIsRefused() {
        assertThatThrownBy(() -> VALIDATOR.validate("0.1.0", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("puckTree must not be null");
    }

    static java.util.stream.Stream<String> hostileTrees() {
        String mb = "x".repeat(1_000_000);
        String manyBad =
                "[" + "{\"type\":\"Heading\",\"props\":{}},".repeat(9_999) + "{\"type\":\"Heading\",\"props\":{}}]";
        return java.util.stream.Stream.of(
                "{\"root\":{\"props\":{}},\"content\":\"text\"}",
                "{\"root\":{\"props\":{}},\"content\":{}}",
                "{\"root\":{\"props\":{}},\"content\":[],\"zones\":[]}",
                "{\"root\":{\"props\":{}},\"content\":[1,null]}",
                "{\"root\":{\"props\":{}},\"content\":[{\"type\":\"Heading\",\"props\":null}]}",
                "{\"root\":null,\"content\":[]}",
                "null",
                "{\"root\":{\"props\":{}},\"content\":[{\"type\":\"Heading\",\"props\":{\"id\":\"h\",\"text\":\"" + mb
                        + "\"}}]}",
                "{\"root\":{\"props\":{}},\"content\":" + manyBad + "}",
                "{\"root\":{\"props\":{}},\"content\":[],\"zones\":{\"a:b\":" + manyBad + "}}");
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.MethodSource("hostileTrees")
    void hostileTreesNeverThrowAndNeverExceedFiftyDetails(String json) {
        for (String version : new String[] {"0.1.0", "test-1"}) {
            List<ErrorDetail> details = VALIDATOR.validate(version, tree(json));
            assertThat(details).hasSizeLessThanOrEqualTo(PuckValidator.MAX_DETAILS);
        }
    }

    @Test
    void tenThousandBadBlocksAreReportedAsTooMany() {
        String manyBad =
                "[" + "{\"type\":\"Video\",\"props\":{}},".repeat(9_999) + "{\"type\":\"Video\",\"props\":{}}]";
        assertThat(fields(VALIDATOR.validate("test-1", tree("{\"root\":{\"props\":{}},\"content\":" + manyBad + "}"))))
                .contains("puckTree/content");
    }
}
