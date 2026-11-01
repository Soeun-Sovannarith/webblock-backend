package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;

import java.sql.Connection;
import java.sql.ResultSet;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

@Tag("integration")
class ProcessedMessagesTest {

    private static PostgresFixture.Database db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshDatabase();
    }

    private static void insert(Connection c, UUID id, String type, String outcome) throws java.sql.SQLException {
        Rows.exec(
                c,
                "INSERT INTO project_messaging.processed_messages (message_id, message_type, outcome, processed_at) "
                        + "VALUES (?, ?, ?, now())",
                id,
                type,
                outcome);
    }

    @Test
    void aMessageIsRecordedOnceAndReadBack() throws Exception {
        try (Connection c = db.asApp()) {
            UUID id = UUID.randomUUID();
            insert(c, id, "project.generation_completed.v1", "applied");
            Rows.assertViolates(
                    "processed_messages_pkey", () -> insert(c, id, "project.generation_completed.v1", "applied"));
        }
        try (Connection c = db.asApp()) {
            UUID id = UUID.randomUUID();
            insert(c, id, "payment.plan_changed.v2", "ignored");
            try (var ps = c.prepareStatement(
                    "SELECT message_type, outcome FROM project_messaging.processed_messages WHERE message_id = ?")) {
                ps.setObject(1, id);
                try (ResultSet rs = ps.executeQuery()) {
                    assertThat(rs.next()).isTrue();
                    assertThat(rs.getString(1)).isEqualTo("payment.plan_changed.v2");
                    assertThat(rs.getString(2)).isEqualTo("ignored");
                }
            }
        }
    }

    @ParameterizedTest
    @ValueSource(
            strings = {"Bad", "project.generation_completed", "project.generation_completed.1", "a.b.v", "Project.x.v1"
            })
    void badMessageTypesAreRefused(String type) throws Exception {
        try (Connection c = db.asApp()) {
            Rows.assertViolates(
                    "processed_messages_message_type_check", () -> insert(c, UUID.randomUUID(), type, "applied"));
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"Applied", "not applied", "applied-1", ""})
    void badOutcomesAreRefused(String outcome) throws Exception {
        try (Connection c = db.asApp()) {
            Rows.assertViolates(
                    "processed_messages_outcome_check",
                    () -> insert(c, UUID.randomUUID(), "project.generation_completed.v1", outcome));
        }
    }

    @Test
    void theApplicationCanOnlyInsertAndSelect() throws Exception {
        try (Connection c = db.asApp()) {
            UUID id = UUID.randomUUID();
            insert(c, id, "project.generation_completed.v1", "applied");
            org.assertj.core.api.Assertions.assertThatThrownBy(() -> Rows.exec(
                            c,
                            "UPDATE project_messaging.processed_messages SET outcome = 'x' WHERE message_id = ?",
                            id))
                    .hasMessageContaining("permission denied");
            org.assertj.core.api.Assertions.assertThatThrownBy(() ->
                            Rows.exec(c, "DELETE FROM project_messaging.processed_messages WHERE message_id = ?", id))
                    .hasMessageContaining("permission denied");
        }
    }
}
