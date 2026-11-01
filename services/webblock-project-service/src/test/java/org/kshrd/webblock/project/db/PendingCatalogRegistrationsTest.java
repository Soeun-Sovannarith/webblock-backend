package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.UUID;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

@Tag("integration")
class PendingCatalogRegistrationsTest {

    private static PreparedStatement insert(Connection c, UUID id, String profile) throws SQLException {
        PreparedStatement ps = c.prepareStatement("INSERT INTO project_messaging.pending_catalog_registrations "
                + "(project_id, business_profile, created_at) VALUES (?, ?::jsonb, now())");
        ps.setObject(1, id);
        ps.setString(2, profile);
        return ps;
    }

    @Test
    void theApplicationMayInsertReadAndDeleteButNeverUpdate() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        UUID id = UUID.randomUUID();
        try (Connection app = db.asApp()) {
            try (PreparedStatement ps = insert(app, id, "{\"businessName\":\"Shop\"}")) {
                assertThat(ps.executeUpdate()).isEqualTo(1);
            }
            try (PreparedStatement ps = app.prepareStatement(
                    "UPDATE project_messaging.pending_catalog_registrations SET created_at = now() WHERE project_id = ?")) {
                ps.setObject(1, id);
                assertThatThrownBy(ps::executeUpdate)
                        .isInstanceOfSatisfying(
                                SQLException.class,
                                e -> assertThat(e.getSQLState()).isEqualTo("42501"));
            }
            try (PreparedStatement ps = app.prepareStatement(
                    "DELETE FROM project_messaging.pending_catalog_registrations WHERE project_id = ?")) {
                ps.setObject(1, id);
                assertThat(ps.executeUpdate()).isEqualTo(1);
            }
        }
    }

    @Test
    void theProfileMustBeAJsonObjectAndTheProjectIdUnique() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        UUID id = UUID.randomUUID();
        try (Connection app = db.asApp()) {
            try (PreparedStatement ps = insert(app, UUID.randomUUID(), "[]")) {
                assertThatThrownBy(ps::executeUpdate)
                        .isInstanceOfSatisfying(
                                SQLException.class,
                                e -> assertThat(e.getSQLState()).isEqualTo("23514"));
            }
            try (PreparedStatement ps = insert(app, id, "{}")) {
                ps.executeUpdate();
            }
            try (PreparedStatement ps = insert(app, id, "{}")) {
                assertThatThrownBy(ps::executeUpdate)
                        .isInstanceOfSatisfying(
                                SQLException.class,
                                e -> assertThat(e.getSQLState()).isEqualTo("23505"));
            }
        }
    }
}
