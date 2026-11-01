package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.FlywayException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogFlyway;

@Tag("integration")
class CatalogMigrationTest {

    private static List<String> strings(Connection c, String sql) throws SQLException {
        List<String> out = new ArrayList<>();
        try (Statement st = c.createStatement();
                ResultSet rs = st.executeQuery(sql)) {
            while (rs.next()) {
                out.add(rs.getString(1));
            }
        }
        return out;
    }

    @Test
    void migratesAsCatalogOwnerWhoOwnsEveryObject() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection c = db.asSuperuser()) {
            assertThat(strings(c, "SELECT tablename || ':' || tableowner FROM pg_tables WHERE schemaname = 'catalog'"))
                    .hasSize(8)
                    .allSatisfy(row -> assertThat(row).endsWith(":catalog_owner"));
            assertThat(strings(c, """
                    SELECT p.proname || ':' || r.rolname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                    JOIN pg_roles r ON r.oid = p.proowner WHERE n.nspname = 'catalog'"""))
                    .hasSize(5)
                    .allSatisfy(row -> assertThat(row).endsWith(":catalog_owner"));
            assertThat(
                            strings(
                                    c,
                                    "SELECT version FROM catalog_flyway.flyway_schema_history WHERE success AND version IS NOT NULL ORDER BY installed_rank"))
                    .startsWith("1", "2", "3", "4", "5");
        }
    }

    @Test
    void refusesToMigrateAsAnotherRole() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(false);
        // Without CREATE on the database Flyway fails earlier (it cannot create its history schema, 42501), before
        // V1's guard runs. Granting it lets the run reach the guard, which is what this test proves.
        try (Connection c = db.asSuperuser();
                Statement st = c.createStatement()) {
            String name = db.url().replaceFirst("^.*/([^/?]+)(\\?.*)?$", "$1");
            st.execute("GRANT CREATE ON DATABASE " + name + " TO " + PostgresFixture.CATALOG_MIGRATOR_LOGIN);
        }
        Flyway withoutRole = Flyway.configure()
                .dataSource(db.url(), PostgresFixture.CATALOG_MIGRATOR_LOGIN, PostgresFixture.PASSWORD)
                .schemas(CatalogFlyway.HISTORY_SCHEMA)
                .defaultSchema(CatalogFlyway.HISTORY_SCHEMA)
                .locations(CatalogFlyway.LOCATION)
                .placeholderReplacement(false)
                .load();
        assertThatThrownBy(withoutRole::migrate)
                .isInstanceOf(FlywayException.class)
                .hasMessageContaining("catalog_owner");
    }

    @Test
    void forcesRowLevelSecurityOnTheFourTenantTablesOnly() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection c = db.asSuperuser()) {
            assertThat(strings(c, """
                    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'catalog' AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity
                    ORDER BY 1"""))
                    .containsExactly("business_profiles", "categories", "product_images", "products");
        }
    }

    @Test
    void currentWebsiteIdKeysOnTheSessionUser() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection c = db.asSuperuser()) {
            assertThat(strings(c, "SELECT pg_get_functiondef('catalog.current_website_id()'::regprocedure)")
                            .getFirst())
                    .contains("session_user")
                    .contains("SECURITY DEFINER");
        }
    }

    /** Indexes behind a primary key, unique key or exclusion are the constraints themselves; any other is extra. */
    private static final String INDEXES_WITHOUT_A_CONSTRAINT = """
            SELECT i.indexname FROM pg_indexes i WHERE i.schemaname = 'catalog'
            AND NOT EXISTS (SELECT 1 FROM pg_constraint k
                            WHERE k.conindid = format('%I.%I', i.schemaname, i.indexname)::regclass)
            ORDER BY 1""";

    /** Notion "6 · Shared catalog" names one index of its own: the GIN on products.attributes. */
    @Test
    @DisplayName("TR-14 (FR-35): the only catalog index that backs no constraint is the GIN on products.attributes")
    void tr14_theOnlyIndexWithoutAConstraintIsTheAttributesGin() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection c = db.asSuperuser()) {
            assertThat(strings(c, INDEXES_WITHOUT_A_CONSTRAINT)).containsExactly("products_attributes_gin");
            assertThat(strings(c, "SELECT indexdef FROM pg_indexes WHERE indexname = 'products_attributes_gin'"))
                    .containsExactly("CREATE INDEX products_attributes_gin ON catalog.products"
                            + " USING gin (attributes jsonb_path_ops)");
        }
    }

    @Test
    @DisplayName("TR-14 (FR-35): a V5 catalog with a product upgrades, keeps its rows and loses products_listing")
    void tr14_aV5CatalogWithAProductUpgradesAndKeepsItsRows() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(false);
        Flyway full =
                CatalogFlyway.configure(db.url(), PostgresFixture.CATALOG_MIGRATOR_LOGIN, PostgresFixture.PASSWORD);
        Flyway.configure()
                .configuration(full.getConfiguration())
                .target("5")
                .load()
                .migrate();
        try (Connection c = db.asSuperuser();
                Statement st = c.createStatement()) {
            assertThat(strings(c, INDEXES_WITHOUT_A_CONSTRAINT))
                    .containsExactly("products_attributes_gin", "products_listing");
            st.execute("""
                    INSERT INTO catalog.business_types VALUES
                      ('00000000-0000-0000-0000-0000000000b1', 'upgrade_shop', 'Upgrade', true, now());
                    INSERT INTO catalog.product_types VALUES
                      ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000b1', 'thing',
                       'Thing', true, now());
                    INSERT INTO catalog.product_type_schemas VALUES
                      ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000c1', 1,
                       '{"type": "object"}', true, now());
                    INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status,
                      storage_limit_bytes, max_image_bytes, storage_used_bytes, created_at, updated_at)
                    VALUES ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
                      'site_upgrade', 'active', 1, 1, 0, now(), now());
                    INSERT INTO catalog.products (product_id, website_id, product_type_schema_id, category_id, name,
                      description, price, attributes, status, sort_order, row_version, created_at, updated_at)
                    VALUES ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000e1',
                      '00000000-0000-0000-0000-0000000000d1', NULL, 'Lamp', NULL, 12.50, '{}', 'published', 3, 1,
                      now(), now())""");
        }
        full.migrate();
        try (Connection c = db.asSuperuser()) {
            assertThat(strings(c, INDEXES_WITHOUT_A_CONSTRAINT)).containsExactly("products_attributes_gin");
            assertThat(strings(
                            c,
                            "SELECT name || ' ' || price || ' ' || status || ' ' || sort_order"
                                    + " FROM catalog.products"))
                    .containsExactly("Lamp 12.50 published 3");
        }
    }

    @Test
    void temporaryTablesAreRevokedFromPublic() throws Exception {
        PostgresFixture.CatalogDatabase db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection c = db.asPlatform();
                Statement st = c.createStatement()) {
            assertThatThrownBy(() -> st.execute("CREATE TEMP TABLE t (x int)"))
                    .isInstanceOfSatisfying(
                            SQLException.class, e -> assertThat(e.getSQLState()).isEqualTo("42501"));
        }
    }
}
