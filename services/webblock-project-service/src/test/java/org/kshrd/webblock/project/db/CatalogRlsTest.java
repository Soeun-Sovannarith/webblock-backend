package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

@Tag("integration")
class CatalogRlsTest {

    static PostgresFixture.CatalogDatabase db;
    static final UUID TYPE = UUID.randomUUID();
    static final UUID A = UUID.randomUUID();
    static final UUID B = UUID.randomUUID();

    static String role(UUID website) {
        return "site_" + website.toString().replace("-", "");
    }

    @BeforeAll
    static void twoWebsites() throws Exception {
        db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection owner = db.asOwner();
                PreparedStatement ps = owner.prepareStatement(
                        "INSERT INTO catalog.business_types VALUES (?, ?, 'Test', true, now())")) {
            ps.setObject(1, TYPE);
            ps.setString(2, "rls_" + TYPE.toString().substring(0, 8) + "_shop");
            ps.executeUpdate();
        }
        try (Connection platform = db.asPlatform()) {
            for (UUID website : new UUID[] {A, B}) {
                try (PreparedStatement ps = platform.prepareStatement("""
                        INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status,
                          storage_limit_bytes, max_image_bytes, storage_used_bytes, created_at, updated_at)
                        VALUES (?, ?, ?, 'active', 104857600, 2097152, 0, now(), now())""")) {
                    ps.setObject(1, website);
                    ps.setObject(2, TYPE);
                    ps.setString(3, role(website));
                    ps.executeUpdate();
                }
                // catalog_platform writes the first profile row of any website (policy business_profiles_platform)
                try (PreparedStatement ps = platform.prepareStatement("""
                        INSERT INTO catalog.business_profiles (website_id, business_name, timezone, opening_hours,
                          created_at, updated_at) VALUES (?, 'Shop', 'UTC', '[]', now(), now())""")) {
                    ps.setObject(1, website);
                    ps.executeUpdate();
                }
            }
        }
        PostgresFixture.createSiteLogin(role(A));
        PostgresFixture.createSiteLogin(role(B));
    }

    private static int count(Connection c, String sql) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(sql);
                ResultSet rs = ps.executeQuery()) {
            rs.next();
            return rs.getInt(1);
        }
    }

    @Test
    void aSiteSeesOnlyItsOwnProfile() throws Exception {
        try (Connection site = db.as(role(A))) {
            assertThat(count(site, "SELECT count(*) FROM catalog.business_profiles"))
                    .isEqualTo(1);
            assertThat(count(site, "SELECT count(*) FROM catalog.business_profiles WHERE website_id = '" + B + "'"))
                    .isZero();
        }
    }

    @Test
    void aSiteCannotWriteAnotherWebsitesRows() throws Exception {
        try (Connection site = db.as(role(A));
                PreparedStatement ps = site.prepareStatement("""
                        INSERT INTO catalog.categories (category_id, website_id, name, slug, sort_order, created_at, updated_at)
                        VALUES (?, ?, 'x', 'x', 0, now(), now())""")) {
            ps.setObject(1, UUID.randomUUID());
            ps.setObject(2, B);
            assertThatThrownBy(ps::executeUpdate)
                    .isInstanceOfSatisfying(
                            SQLException.class, e -> assertThat(e.getSQLState()).isEqualTo("42501"));
        }
    }

    /** Notion "6 · Shared catalog": the site's CMS edits its business profile, which is never deleted. */
    @Test
    @DisplayName("TR-15 (FR-35): a site may not delete its own business profile; the row stays")
    void tr15_aSiteCannotDeleteItsOwnBusinessProfile() throws Exception {
        try (Connection site = db.as(role(A));
                PreparedStatement ps =
                        site.prepareStatement("DELETE FROM catalog.business_profiles WHERE website_id = ?")) {
            ps.setObject(1, A);
            assertThatThrownBy(ps::executeUpdate)
                    .isInstanceOfSatisfying(
                            SQLException.class, e -> assertThat(e.getSQLState()).isEqualTo("42501"));
            assertThat(count(site, "SELECT count(*) FROM catalog.business_profiles WHERE website_id = '" + A + "'"))
                    .isEqualTo(1);
        }
    }

    @Test
    void aSiteHasNoAccessToWebsites() throws Exception {
        try (Connection site = db.as(role(A))) {
            assertThatThrownBy(() -> count(site, "SELECT count(*) FROM catalog.websites"))
                    .isInstanceOfSatisfying(
                            SQLException.class, e -> assertThat(e.getSQLState()).isEqualTo("42501"));
        }
    }

    @Test
    void archivingAWebsiteLocksItsSiteOut() throws Exception {
        UUID c = UUID.randomUUID();
        try (Connection platform = db.asPlatform();
                PreparedStatement ps = platform.prepareStatement("""
                        INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status,
                          storage_limit_bytes, max_image_bytes, storage_used_bytes, created_at, updated_at)
                        VALUES (?, ?, ?, 'active', 1, 1, 0, now(), now())""")) {
            ps.setObject(1, c);
            ps.setObject(2, TYPE);
            ps.setString(3, role(c));
            ps.executeUpdate();
            try (PreparedStatement profile = platform.prepareStatement("""
                    INSERT INTO catalog.business_profiles (website_id, business_name, timezone, opening_hours,
                      created_at, updated_at) VALUES (?, 'C', 'UTC', '[]', now(), now())""")) {
                profile.setObject(1, c);
                profile.executeUpdate();
            }
            try (PreparedStatement archive =
                    platform.prepareStatement("UPDATE catalog.websites SET status = 'archived' WHERE website_id = ?")) {
                archive.setObject(1, c);
                assertThat(archive.executeUpdate()).isEqualTo(1);
            }
        }
        PostgresFixture.createSiteLogin(role(c));
        try (Connection site = db.as(role(c))) {
            assertThat(count(site, "SELECT count(*) FROM catalog.business_profiles"))
                    .isZero();
        }
    }
}
