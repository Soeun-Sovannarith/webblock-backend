package org.kshrd.webblock.project.application.access.model;

import static org.assertj.core.api.Assertions.assertThat;

import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;
import java.util.stream.Collectors;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * AccessAction is the one list of accepted actions, and it equals access/actions.v1.json of the web-block-contracts
 * commit in contracts.lock (TR-27, design G20). Only scripts/contracts-sync.sh writes contracts/ and its --check
 * proves the copy (TR-11), so this test reads the pinned file itself.
 */
class AccessActionListTest {

    private static final Path LIST = Path.of("contracts/access/actions.v1.json");
    private static final Path SCHEMA = Path.of("contracts/access/actions.v1.schema.json");
    private static final JsonMapper JSON = JsonMapper.builder().build();

    private static JsonNode read(Path file) throws IOException {
        return JSON.readTree(Files.readString(file));
    }

    private static Schema schema() throws IOException {
        return SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12)
                .getSchema(read(SCHEMA));
    }

    @Test
    @DisplayName("FR-32.1, TR-27: the enum equals actions.v1.json, names and write flags")
    void fr32_1_theEnumEqualsTheSharedList() throws IOException {
        Map<String, Boolean> shared = new HashMap<>();
        for (JsonNode action : read(LIST).required("actions")) {
            Boolean before = shared.put(
                    action.required("name").asString(), action.required("write").booleanValue());
            assertThat(before).as("names are unique").isNull();
        }

        Map<String, Boolean> accepted = Arrays.stream(AccessAction.values())
                .collect(Collectors.toMap(AccessAction::wireName, AccessAction::isWrite));

        assertThat(accepted).isEqualTo(shared);
        assertThat(shared).containsEntry("manage_site", true);
    }

    @Test
    @DisplayName("FR-32, TR-27: actions.v1.json passes actions.v1.schema.json")
    void fr32_theFilePassesItsSchema() throws IOException {
        assertThat(schema().validate(read(LIST))).isEmpty();
    }

    /** The schema check above means something only if the schema refuses a broken list. */
    @ParameterizedTest
    @ValueSource(
            strings = {
                "{\"actions\":[]}",
                "{\"actions\":[{\"name\":\"deploy_release\",\"admin_allowed\":false}]}",
                "{\"actions\":[{\"name\":\"deploy_release\",\"write\":\"true\",\"admin_allowed\":false}]}",
                "{\"actions\":[{\"name\":\"Deploy_Release\",\"write\":true,\"admin_allowed\":false}]}",
                "{\"actions\":[{\"name\":\"deploy_release\",\"write\":true,\"admin_allowed\":false,\"read\":false}]}",
                "{\"actions\":[{\"name\":\"deploy_release\",\"write\":true,\"admin_allowed\":false}],\"kind\":1}"
            })
    @DisplayName("TR-27: the schema refuses an empty list, a missing or mistyped flag, a bad name, an extra field")
    void tr27_theSchemaRefusesABrokenList(String list) throws IOException {
        assertThat(schema().validate(JSON.readTree(list))).isNotEmpty();
    }
}
