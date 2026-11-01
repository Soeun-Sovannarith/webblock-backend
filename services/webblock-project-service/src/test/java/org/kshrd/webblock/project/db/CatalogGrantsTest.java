package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * The catalog's tenant boundary, locked exactly: every privilege any role other than the owner holds in schema catalog
 * (tables, columns, functions, the schema itself), every row-level security policy, and FORCE ROW LEVEL SECURITY on the
 * four tenant tables. A later migration that widens any of them fails here until this list is changed on purpose.
 */
@Tag("integration")
class CatalogGrantsTest {

    static PostgresFixture.CatalogDatabase db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshCatalogDatabase(true);
    }

    private static List<String> rows(String sql) throws Exception {
        try (Connection c = db.asSuperuser();
                Statement st = c.createStatement();
                ResultSet rs = st.executeQuery(sql)) {
            List<String> rows = new ArrayList<>();
            while (rs.next()) {
                rows.add(rs.getString(1));
            }
            return rows;
        }
    }

    /** The grantee's name, or PUBLIC; the owner's own privileges are left out. */
    private static final String GRANTEE = "CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END";

    @Test
    void tablePrivilegesAreExactlyTheDocumentedOnes() throws Exception {
        assertThat(rows("SELECT " + GRANTEE + " || ' ' || a.privilege_type || ' ' || c.relname"
                        + " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                        + " CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a"
                        + " WHERE n.nspname = 'catalog' AND a.grantee <> c.relowner"))
                .containsExactlyInAnyOrder(
                        "catalog_platform INSERT websites",
                        "catalog_platform SELECT websites",
                        "catalog_platform INSERT business_profiles",
                        "catalog_platform SELECT business_types",
                        "catalog_platform SELECT product_types",
                        "catalog_platform SELECT product_type_schemas",
                        "catalog_site SELECT business_types",
                        "catalog_site SELECT product_types",
                        "catalog_site SELECT product_type_schemas",
                        "catalog_site SELECT business_profiles",
                        "catalog_site INSERT business_profiles",
                        "catalog_site UPDATE business_profiles",
                        "catalog_site SELECT categories",
                        "catalog_site INSERT categories",
                        "catalog_site UPDATE categories",
                        "catalog_site DELETE categories",
                        "catalog_site SELECT products",
                        "catalog_site INSERT products",
                        "catalog_site UPDATE products",
                        "catalog_site DELETE products",
                        "catalog_site SELECT product_images",
                        "catalog_site INSERT product_images",
                        "catalog_site UPDATE product_images",
                        "catalog_site DELETE product_images");
    }

    @Test
    void columnPrivilegesAreOnlyThePlatformsThreeWebsiteUpdates() throws Exception {
        assertThat(rows("SELECT " + GRANTEE + " || ' ' || a.privilege_type || ' ' || c.relname || '.' || att.attname"
                        + " FROM pg_attribute att JOIN pg_class c ON c.oid = att.attrelid"
                        + " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        + " CROSS JOIN LATERAL aclexplode(att.attacl) a"
                        + " WHERE n.nspname = 'catalog' AND att.attacl IS NOT NULL"))
                .containsExactlyInAnyOrder(
                        "catalog_platform UPDATE websites.status",
                        "catalog_platform UPDATE websites.storage_limit_bytes",
                        "catalog_platform UPDATE websites.max_image_bytes");
    }

    @Test
    void onlySitesMayCallCurrentWebsiteIdAndNothingIsExecutableByPublic() throws Exception {
        assertThat(rows("SELECT " + GRANTEE + " || ' ' || a.privilege_type || ' ' || p.proname"
                        + " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                        + " CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a"
                        + " WHERE n.nspname = 'catalog' AND a.grantee <> p.proowner"))
                .containsExactly("catalog_site EXECUTE current_website_id");
    }

    /**
     * PostgreSQL gives PUBLIC USAGE on a new type by default, including the row type it makes for every table; Notion
     * gives no role but the four it lists anything, so nobody but the owner holds a privilege on the two catalog enums,
     * the row types of the eight catalog tables or the row type of the catalog's Flyway history table.
     */
    @Test
    @DisplayName("TR-15 (FR-35): PUBLIC and every other role hold no privilege on the catalog enums or table row types")
    void tr15_nobodyButTheOwnerHoldsAPrivilegeOnTheCatalogEnums() throws Exception {
        assertThat(rows(
                        "SELECT n.nspname || '.' || t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
                                + " WHERE n.nspname IN ('catalog', 'catalog_flyway') AND t.typtype IN ('e', 'c')"))
                .containsExactlyInAnyOrder(
                        "catalog.product_status",
                        "catalog.website_status",
                        "catalog.business_types",
                        "catalog.product_types",
                        "catalog.product_type_schemas",
                        "catalog.websites",
                        "catalog.business_profiles",
                        "catalog.categories",
                        "catalog.products",
                        "catalog.product_images",
                        "catalog_flyway.flyway_schema_history");
        assertThat(rows("SELECT " + GRANTEE + " || ' ' || a.privilege_type || ' ' || t.typname"
                        + " FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
                        + " CROSS JOIN LATERAL aclexplode(coalesce(t.typacl, acldefault('T', t.typowner))) a"
                        + " WHERE n.nspname IN ('catalog', 'catalog_flyway') AND t.typtype IN ('e', 'c')"
                        + " AND a.grantee <> t.typowner"))
                .isEmpty();
    }

    @Test
    void bothRuntimeRolesMayOnlyUseTheSchema() throws Exception {
        assertThat(rows("SELECT " + GRANTEE + " || ' ' || a.privilege_type"
                        + " FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a"
                        + " WHERE n.nspname = 'catalog' AND a.grantee <> n.nspowner"))
                .containsExactlyInAnyOrder("catalog_platform USAGE", "catalog_site USAGE");
    }

    @Test
    void theRowLevelSecurityPoliciesAreExactlyTheDocumentedOnes() throws Exception {
        assertThat(rows(
                        "SELECT tablename || ' ' || policyname || ' ' || permissive || ' ' || array_to_string(roles, ',')"
                                + " || ' ' || cmd || ' USING ' || coalesce(qual, '-') || ' CHECK ' || coalesce(with_check, '-')"
                                + " FROM pg_policies WHERE schemaname = 'catalog'"))
                .containsExactlyInAnyOrder(
                        "business_profiles business_profiles_own_website PERMISSIVE catalog_site ALL" + " USING "
                                + OWN_WEBSITE + " CHECK " + OWN_WEBSITE,
                        "business_profiles business_profiles_platform PERMISSIVE catalog_platform ALL"
                                + " USING true CHECK true",
                        "categories categories_own_website PERMISSIVE catalog_site ALL" + " USING " + OWN_WEBSITE
                                + " CHECK " + OWN_WEBSITE,
                        "products products_own_website PERMISSIVE catalog_site ALL" + " USING " + OWN_WEBSITE
                                + " CHECK " + OWN_WEBSITE,
                        "product_images product_images_own_website PERMISSIVE catalog_site ALL" + " USING "
                                + OWN_WEBSITE + " CHECK " + OWN_WEBSITE);
    }

    private static final String OWN_WEBSITE =
            "(website_id = ( SELECT catalog.current_website_id() AS current_website_id))";

    @Test
    void exactlyTheFourTenantTablesEnableAndForceRowLevelSecurity() throws Exception {
        assertThat(rows("SELECT c.relname || ' enable=' || c.relrowsecurity || ' force=' || c.relforcerowsecurity"
                        + " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                        + " WHERE n.nspname = 'catalog' AND c.relkind IN ('r', 'p')"
                        + " AND (c.relrowsecurity OR c.relforcerowsecurity)"))
                .containsExactlyInAnyOrder(
                        "business_profiles enable=true force=true",
                        "categories enable=true force=true",
                        "products enable=true force=true",
                        "product_images enable=true force=true");
    }
}
