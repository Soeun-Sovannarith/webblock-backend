package org.kshrd.webblock.project.application.page.handler;

import com.networknt.schema.Error;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Checks a Puck tree against the component catalog of the project's component_catalog_version (spec 5.2). Every
 * bundled catalog, classpath:component-catalog/<version>/catalog.schema.json (JSON Schema 2020-12), is loaded and
 * compiled once at start-up. Each problem is reported as field "puckTree" + JSON pointer with code
 * PUCK_TREE_INVALID; the validator's own message, which can quote content, is never used.
 */
@Component
public class PuckValidator {

    public static final int MAX_DETAILS = 50;
    static final int MAX_FIELD_LENGTH = 300;
    private static final int MAX_BLOCKS = 200;
    static final String PATTERN = "classpath*:component-catalog/*/catalog.schema.json";
    private static final Pattern VERSION = Pattern.compile("^[0-9A-Za-z][0-9A-Za-z.-]{0,99}$");

    private record Catalog(JsonNode document, Schema schema) {}

    private final Map<String, Catalog> byVersion;

    public PuckValidator(JsonMapper jsonMapper) throws IOException {
        SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);
        Map<String, Catalog> loaded = new HashMap<>();
        for (Resource resource : new PathMatchingResourcePatternResolver().getResources(PATTERN)) {
            String path = resource.getURL().getPath();
            String folder = path.substring(0, path.lastIndexOf('/'));
            String version = folder.substring(folder.lastIndexOf('/') + 1);
            if (!VERSION.matcher(version).matches()) {
                throw new IllegalStateException("Invalid component catalog version folder: " + version);
            }
            JsonNode document;
            try (InputStream in = resource.getInputStream()) {
                document = jsonMapper.readTree(in);
            }
            if (loaded.put(version, new Catalog(document, registry.getSchema(document))) != null) {
                throw new IllegalStateException("Component catalog " + version + " is defined twice");
            }
        }
        if (loaded.isEmpty()) {
            throw new IllegalStateException("No component catalog found at " + PATTERN);
        }
        this.byVersion = Map.copyOf(loaded);
    }

    private Optional<Catalog> find(String version) {
        return version == null ? Optional.empty() : Optional.ofNullable(byVersion.get(version));
    }

    public boolean knows(String version) {
        return find(version).isPresent();
    }

    public Optional<JsonNode> catalogDocument(String version) {
        return find(version).map(Catalog::document);
    }

    /**
     * Returns the problems of a tree, at most {@link #MAX_DETAILS}, sorted. A Java null tree is a caller bug
     * (IllegalArgumentException); a JSON null or any other malformed shape is an ordinary validation problem. Oversized
     * block arrays are cut before validating, so a hostile tree cannot make the validator allocate an error per block.
     */
    public List<ErrorDetail> validate(String version, JsonNode puckTree) {
        if (puckTree == null) {
            throw new IllegalArgumentException("puckTree must not be null");
        }
        Catalog catalog = find(version)
                .orElseThrow(() -> new IllegalStateException("Unknown component catalog version " + version));
        return catalog.schema().validate(bounded(puckTree)).stream()
                .map(PuckValidator::field)
                .distinct()
                .sorted()
                .limit(MAX_DETAILS)
                .map(field -> ErrorDetail.of(field, ErrorCode.PUCK_TREE_INVALID))
                .toList();
    }

    /** Keeps at most MAX_BLOCKS + 1 entries of content, zones and each zone: more is already a maxItems error. */
    private static JsonNode bounded(JsonNode tree) {
        if (!(tree instanceof ObjectNode object)) {
            return tree;
        }
        ObjectNode copy = object.objectNode().setAll(object);
        JsonNode content = object.get("content");
        if (content instanceof ArrayNode array && array.size() > MAX_BLOCKS) {
            copy.set("content", head(array));
        }
        if (object.get("zones") instanceof ObjectNode zones && zones.size() > 0) {
            ObjectNode cut = zones.objectNode().setAll(zones);
            int kept = 0;
            for (var entry : zones.properties()) {
                if (kept++ > MAX_BLOCKS) {
                    cut.remove(entry.getKey());
                } else if (entry.getValue() instanceof ArrayNode array && array.size() > MAX_BLOCKS) {
                    cut.set(entry.getKey(), head(array));
                }
            }
            copy.set("zones", cut);
        }
        return copy;
    }

    private static ArrayNode head(ArrayNode array) {
        ArrayNode head = array.arrayNode();
        for (int i = 0; i <= MAX_BLOCKS; i++) {
            head.add(array.get(i));
        }
        return head;
    }

    static String field(Error error) {
        String pointer = error.getInstanceLocation().toString();
        String property = error.getProperty();
        // The property of "required" comes from the catalog; for "additionalProperties" it is the client's key, so
        // only the parent is reported.
        if (property != null && "required".equals(error.getKeyword())) {
            pointer = pointer + "/" + property.replace("~", "~0").replace("/", "~1");
        }
        String field = "puckTree" + pointer;
        return field.length() <= MAX_FIELD_LENGTH ? field : field.substring(0, MAX_FIELD_LENGTH);
    }
}
