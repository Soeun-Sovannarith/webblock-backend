package org.kshrd.webblock.project.db;

import java.sql.Connection;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

@Tag("integration")
class GenerationTablesTest {

    private static PostgresFixture.Database db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshDatabase();
    }

    @Test
    void contractVersionsArePositiveAndUnique() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            Rows.contract(c, p, 1);
            Rows.assertViolates("generation_contracts_version_key", () -> Rows.contract(c, p, 1));
            Rows.assertViolates("generation_contracts_version_check", () -> Rows.contract(c, p, 0));
        }
    }

    @Test
    void onlyOneRequestInFlightPerProject() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID k = Rows.contract(c, p, 1);
            Rows.request(c, p, k, "key-1", "captured");
            Rows.assertViolates(
                    "generation_requests_one_in_flight", () -> Rows.request(c, p, k, "key-2", "generating"));
            Rows.request(c, p, k, "key-3", "failed");
        }
    }

    @Test
    void idempotencyKeysAreUniquePerProject() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID k = Rows.contract(c, p, 1);
            Rows.request(c, p, k, "same", "failed");
            Rows.assertViolates("generation_requests_idempotency_key", () -> Rows.request(c, p, k, "same", "failed"));
            UUID other = Rows.project(c);
            Rows.request(c, other, Rows.contract(c, other, 1), "same", "failed");
        }
    }

    @Test
    void aRequestUsesItsOwnProjectsContract() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID foreignContract = Rows.contract(c, Rows.project(c), 1);
            Rows.assertViolates(
                    "generation_requests_contract_fk", () -> Rows.request(c, p, foreignContract, "k", "failed"));
        }
    }

    @Test
    void publishedExactlyWhenACommitIsRecorded() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID r = Rows.request(c, p, Rows.contract(c, p, 1), "k", "publishing");
            Rows.assertViolates(
                    "generation_requests_published_has_commit",
                    () -> Rows.exec(
                            c,
                            "UPDATE project.generation_requests SET status = 'published' "
                                    + "WHERE generation_request_id = ?",
                            r));
            Rows.assertViolates(
                    "generation_requests_source_commit_sha_check",
                    () -> Rows.exec(
                            c,
                            "UPDATE project.generation_requests SET status = 'published', source_commit_sha = 'XYZ' "
                                    + "WHERE generation_request_id = ?",
                            r));
            Rows.exec(
                    c,
                    "UPDATE project.generation_requests SET status = 'published', source_commit_sha = ? "
                            + "WHERE generation_request_id = ?",
                    "c".repeat(40),
                    r);
            Rows.assertViolates(
                    "generation_requests_commit_write_once",
                    () -> Rows.exec(
                            c,
                            "UPDATE project.generation_requests SET source_commit_sha = ? "
                                    + "WHERE generation_request_id = ?",
                            "d".repeat(40),
                            r));
        }
    }

    @Test
    void capturedInputIsFrozen() throws Exception {
        // The app role may not update frozen columns (V3 column grants); the superuser proves the trigger.
        try (Connection c = db.asApp();
                Connection su = db.asSuperuser()) {
            UUID p = Rows.project(c);
            UUID r = Rows.request(c, p, Rows.contract(c, p, 1), "k", "captured");
            Rows.assertViolates(
                    "generation_requests_capture_immutable",
                    () -> Rows.exec(
                            su,
                            "UPDATE project.generation_requests SET captured_input = '{\"x\":1}'::jsonb "
                                    + "WHERE generation_request_id = ?",
                            r));
            Rows.assertViolates(
                    "generation_requests_capture_immutable",
                    () -> Rows.exec(
                            su,
                            "UPDATE project.generation_requests SET request_hash = ? "
                                    + "WHERE generation_request_id = ?",
                            "e".repeat(64),
                            r));
        }
    }

    @Test
    void hashesAreLowercaseHex() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            UUID k = Rows.contract(c, p, 1);
            Rows.assertViolates(
                    "generation_requests_request_hash_check",
                    () -> Rows.exec(
                            c,
                            "INSERT INTO project.generation_requests (generation_request_id, project_id, "
                                    + "idempotency_key, request_hash, generation_contract_id, captured_edit_revision, "
                                    + "captured_input, captured_input_hash, status, created_at, updated_at) "
                                    + "VALUES (?, ?, 'h', ?, ?, 0, '{}'::jsonb, ?, 'failed', now(), now())",
                            UUID.randomUUID(),
                            p,
                            "A".repeat(64),
                            k,
                            "b".repeat(64)));
        }
    }
}
