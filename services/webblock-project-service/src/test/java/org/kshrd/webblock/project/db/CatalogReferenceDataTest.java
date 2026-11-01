package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import tools.jackson.databind.json.JsonMapper;

@Tag("integration")
class CatalogReferenceDataTest {

    static PostgresFixture.CatalogDatabase db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshCatalogDatabase(true);
    }

    private static List<String> strings(String sql) throws SQLException {
        List<String> out = new ArrayList<>();
        try (Connection c = db.asSuperuser();
                Statement st = c.createStatement();
                ResultSet rs = st.executeQuery(sql)) {
            while (rs.next()) {
                out.add(rs.getString(1));
            }
        }
        return out;
    }

    @Test
    void theRepeatableSeedWasApplied() throws Exception {
        assertThat(
                        strings(
                                "SELECT description FROM catalog_flyway.flyway_schema_history WHERE version IS NULL AND success"))
                .contains("catalog reference data");
    }

    @Test
    void thereIsAtLeastOneActiveBusinessTypeAndEveryCodeUsesTheShopForm() throws Exception {
        assertThat(strings("SELECT code FROM catalog.business_types WHERE is_active"))
                .isNotEmpty();
        assertThat(strings("SELECT code FROM catalog.business_types"))
                .allSatisfy(code -> assertThat(code).matches("^[a-z][a-z0-9_]*_shop$"));
        assertThat(strings("SELECT code FROM catalog.product_types"))
                .allSatisfy(code -> assertThat(code).matches("^[a-z][a-z0-9_]*$"));
    }

    @Test
    void everyActiveProductTypeHasAnActiveSchema() throws Exception {
        assertThat(strings("""
                SELECT t.code FROM catalog.product_types t WHERE t.is_active AND NOT EXISTS (
                  SELECT 1 FROM catalog.product_type_schemas s WHERE s.product_type_id = t.product_type_id AND s.is_active)""")).isEmpty();
    }

    @Test
    void everyFieldSchemaIsAJsonSchemaObject() throws Exception {
        SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);
        for (String schema : strings("SELECT field_schema::text FROM catalog.product_type_schemas")) {
            assertThat(registry.getSchema(schema)).isNotNull();
            assertThat(schema).contains("\"type\": \"object\"");
        }
    }

    @Test
    @DisplayName("FR-8: five active business types, clothing_shop renamed Clothes")
    void fr8_fiveActiveBusinessTypes() throws Exception {
        assertThat(strings("SELECT code || '=' || name FROM catalog.business_types WHERE is_active"))
                .containsExactlyInAnyOrder(
                        "clothing_shop=Clothes",
                        "food_shop=Foods",
                        "beauty_shop=Beauty",
                        "furniture_shop=Furniture",
                        "tech_shop=Technology");
    }

    @Test
    @DisplayName("FR-8: each new business type has one product type with an active version 1 schema")
    void fr8_newTypesHaveOneProductTypeEach() throws Exception {
        assertThat(strings("""
                SELECT b.code || '/' || t.code || '/' || s.version_number
                FROM catalog.business_types b
                JOIN catalog.product_types t USING (business_type_id)
                JOIN catalog.product_type_schemas s USING (product_type_id)
                WHERE b.code <> 'clothing_shop' AND t.is_active AND s.is_active"""))
                .containsExactlyInAnyOrder(
                        "food_shop/menu_item/1",
                        "beauty_shop/beauty_product/1",
                        "furniture_shop/furniture_item/1",
                        "tech_shop/tech_device/1");
    }

    @Test
    @DisplayName("FR-8: the new schemas accept a sample attributes object and refuse an unknown field")
    void fr8_newSchemasValidateSamples() throws Exception {
        JsonMapper json = JsonMapper.builder().build();
        SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);
        String[][] samples = {
            {
                "menu_item",
                "{\"portion\":\"1 bowl\",\"spice_level\":\"hot\",\"dietary\":[\"halal\",\"vegan\"]}",
                "{\"spice_level\":\"extreme\"}"
            },
            {
                "beauty_product",
                "{\"volume\":\"50 ml\",\"skin_type\":[\"dry\"],\"ingredients\":\"water\"}",
                "{\"skin_type\":[\"scaly\"]}"
            },
            {"furniture_item", "{\"material\":\"oak\",\"dimensions\":\"80x40\",\"color\":\"brown\"}", "{\"weight\":1}"},
            {
                "tech_device",
                "{\"brand\":\"A\",\"model\":\"B\",\"storage\":\"256 GB\",\"color\":\"black\",\"warranty_months\":12}",
                "{\"warranty_months\":-1}"
            },
        };
        for (String[] sample : samples) {
            String schema = strings("SELECT s.field_schema::text FROM catalog.product_type_schemas s "
                            + "JOIN catalog.product_types t USING (product_type_id) WHERE t.code = '" + sample[0] + "'")
                    .getFirst();
            var compiled = registry.getSchema(schema);
            assertThat(compiled.validate(json.readTree(sample[1])))
                    .as(sample[0])
                    .isEmpty();
            assertThat(compiled.validate(json.readTree(sample[2])))
                    .as(sample[0])
                    .isNotEmpty();
        }
    }

    @Test
    void theSeedCanRunAgainWithoutChangingAnything() throws Exception {
        String seed = new ClassPathResource("db/migration/catalog/R__catalog_reference_data.sql")
                .getContentAsString(StandardCharsets.UTF_8);
        List<String> before = strings("SELECT count(*) || '/' || (SELECT count(*) FROM catalog.product_types) || '/' "
                + "|| (SELECT count(*) FROM catalog.product_type_schemas) FROM catalog.business_types");
        try (Connection owner = db.asOwner();
                Statement st = owner.createStatement()) {
            st.execute(seed);
        }
        assertThat(strings("SELECT count(*) || '/' || (SELECT count(*) FROM catalog.product_types) || '/' "
                        + "|| (SELECT count(*) FROM catalog.product_type_schemas) FROM catalog.business_types"))
                .isEqualTo(before);
    }

    @Test
    void changingAPublishedFieldSchemaFailsLoudly() throws Exception {
        String id = strings("SELECT product_type_schema_id FROM catalog.product_type_schemas LIMIT 1")
                .getFirst();
        try (Connection owner = db.asOwner();
                PreparedStatement ps = owner.prepareStatement("""
                        INSERT INTO catalog.product_type_schemas
                          (product_type_schema_id, product_type_id, version_number, field_schema, is_active, created_at)
                        SELECT product_type_schema_id, product_type_id, version_number, '{"type":"object"}', is_active, now()
                        FROM catalog.product_type_schemas WHERE product_type_schema_id = ?::uuid
                        ON CONFLICT (product_type_schema_id) DO UPDATE SET field_schema = EXCLUDED.field_schema""")) {
            ps.setString(1, id);
            assertThatThrownBy(ps::executeUpdate)
                    .isInstanceOfSatisfying(
                            SQLException.class, e -> assertThat(e.getSQLState()).isEqualTo("55000"));
        }
    }
}
