package org.kshrd.webblock.project.db;

import java.sql.Connection;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

@Tag("integration")
class ProjectConstraintsTest {

    private static PostgresFixture.Database db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshDatabase();
    }

    private static void setRepo(Connection c, UUID p, long id) throws java.sql.SQLException {
        Rows.exec(
                c,
                "UPDATE project.projects SET repository_id = ?, repository_full_name = 'org/repo' "
                        + "WHERE project_id = ?",
                id,
                p);
    }

    @Test
    @DisplayName("FR-36.4: a repository_id links to one project")
    void aRepositoryLinksToOneProject() throws Exception {
        try (Connection c = db.asApp()) {
            UUID a = Rows.project(c);
            UUID b = Rows.project(c);
            setRepo(c, a, 777);
            Rows.assertViolates("projects_repository_id_key", () -> setRepo(c, b, 777));
        }
    }

    @ParameterizedTest
    @ValueSource(longs = {0, -5})
    @DisplayName("FR-36.4: a repository_id is above 0")
    void repositoryIdsArePositive(long id) throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            Rows.assertViolates("projects_repository_id_check", () -> setRepo(c, p, id));
        }
    }

    @Test
    void editRevisionNeverGoesNegative() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            Rows.assertViolates(
                    "projects_edit_revision_check",
                    () -> Rows.exec(c, "UPDATE project.projects SET edit_revision = -1 WHERE project_id = ?", p));
            Rows.exec(c, "UPDATE project.projects SET edit_revision = edit_revision + 1 WHERE project_id = ?", p);
        }
    }

    @Test
    void capturedInputHashIsLowercaseHex() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID k = Rows.contract(c, p, 1);
            Rows.assertViolates(
                    "generation_requests_captured_input_hash_check",
                    () -> Rows.exec(
                            c,
                            "INSERT INTO project.generation_requests (generation_request_id, project_id, "
                                    + "idempotency_key, request_hash, generation_contract_id, captured_edit_revision, "
                                    + "captured_input, captured_input_hash, status, created_at, updated_at) "
                                    + "VALUES (?, ?, 'h', ?, ?, 0, '{}'::jsonb, ?, 'failed', now(), now())",
                            UUID.randomUUID(),
                            p,
                            "a".repeat(64),
                            k,
                            "G".repeat(64)));
        }
    }

    @Test
    void aCommitShaNeedsPublishedStatus() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID r = Rows.request(c, p, Rows.contract(c, p, 1), "k", "failed");
            Rows.assertViolates(
                    "generation_requests_published_has_commit",
                    () -> Rows.exec(
                            c,
                            "UPDATE project.generation_requests SET source_commit_sha = ? "
                                    + "WHERE generation_request_id = ?",
                            "c".repeat(40),
                            r));
        }
    }

    @Test
    void theOtherCapturedFieldsAreFrozenToo() throws Exception {
        // The app role may not update frozen columns (V3 column grants); the superuser proves the trigger.
        try (Connection c = db.asApp();
                Connection su = db.asSuperuser()) {
            UUID p = Rows.project(c);
            UUID r = Rows.request(c, p, Rows.contract(c, p, 1), "k", "captured");
            Rows.assertViolates(
                    "generation_requests_capture_immutable",
                    () -> Rows.exec(
                            su,
                            "UPDATE project.generation_requests SET captured_input_hash = ? "
                                    + "WHERE generation_request_id = ?",
                            "f".repeat(64),
                            r));
            Rows.assertViolates(
                    "generation_requests_capture_immutable",
                    () -> Rows.exec(
                            su,
                            "UPDATE project.generation_requests SET captured_edit_revision = 9 "
                                    + "WHERE generation_request_id = ?",
                            r));
        }
    }
}
