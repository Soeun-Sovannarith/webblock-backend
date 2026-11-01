package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

@Tag("integration")
class ProjectsAndPagesTest {

    private static PostgresFixture.Database db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshDatabase();
    }

    @Test
    @DisplayName("FR-36.4: repository_id and repository_full_name are both set or both empty")
    void repositoryIdAndNameComeTogether() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            Rows.assertViolates(
                    "projects_repository_pair",
                    () -> Rows.exec(c, "UPDATE project.projects SET repository_id = 42 WHERE project_id = ?", p));
        }
    }

    @Test
    void businessTypeNeverChanges() throws Exception {
        // The app role may not update frozen columns (V3 column grants); the superuser proves the trigger.
        try (Connection c = db.asApp();
                Connection su = db.asSuperuser()) {
            UUID p = Rows.project(c);
            Rows.assertViolates(
                    "projects_business_type_immutable",
                    () -> Rows.exec(
                            su,
                            "UPDATE project.projects SET business_type_id = ? WHERE project_id = ?",
                            UUID.randomUUID(),
                            p));
        }
    }

    @Test
    void deletedAtIsWriteOnce() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            Rows.exec(c, "UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", p);
            Rows.assertViolates(
                    "projects_deleted_at_write_once",
                    () -> Rows.exec(
                            c,
                            "UPDATE project.projects SET deleted_at = now() + interval '1 day' WHERE project_id = ?",
                            p));
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"/", "/products", "/a/b-c"})
    void validPathsAreStored(String path) throws Exception {
        try (Connection c = db.asApp()) {
            UUID page = Rows.page(c, Rows.project(c), path);
            try (PreparedStatement ps = c.prepareStatement("SELECT path FROM project.pages WHERE page_id = ?")) {
                ps.setObject(1, page);
                try (ResultSet rs = ps.executeQuery()) {
                    assertThat(rs.next()).isTrue();
                    assertThat(rs.getString(1)).isEqualTo(path);
                }
            }
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"products", "/Products", "/a/", "//", "/a_b"})
    void invalidPathsAreRefused(String path) throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            Rows.assertViolates("pages_path_check", () -> Rows.page(c, p, path));
        }
    }

    @Test
    void aPathIsUniqueAmongLivePagesAndFreedBySoftDelete() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID first = Rows.page(c, p, "/about");
            Rows.assertViolates("pages_project_path_key", () -> Rows.page(c, p, "/about"));
            Rows.exec(c, "UPDATE project.pages SET deleted_at = now() WHERE page_id = ?", first);
            Rows.page(c, p, "/about");
        }
    }
}
