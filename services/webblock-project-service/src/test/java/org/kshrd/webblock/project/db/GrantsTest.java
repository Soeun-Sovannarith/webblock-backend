package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.ResultSet;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.postgresql.util.PSQLException;

@Tag("integration")
class GrantsTest {

    private static PostgresFixture.Database db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshDatabase();
    }

    @Test
    void theApplicationCannotDelete() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            assertThatThrownBy(() -> Rows.exec(c, "DELETE FROM project.projects WHERE project_id = ?", p))
                    .isInstanceOf(PSQLException.class)
                    .hasMessageContaining("permission denied");
        }
    }

    @Test
    void theApplicationCannotChangeTheSchema() throws Exception {
        try (Connection c = db.asApp()) {
            assertThatThrownBy(() -> Rows.exec(c, "CREATE TABLE project.sneaky (id int)"))
                    .hasMessageContaining("permission denied");
            assertThatThrownBy(
                            () -> Rows.exec(c, "ALTER TABLE project.projects DROP CONSTRAINT projects_repository_pair"))
                    .hasMessageContaining("must be owner");
        }
    }

    @Test
    void theApplicationCannotReadMigrationHistory() throws Exception {
        try (Connection c = db.asApp()) {
            assertThatThrownBy(
                            () -> c.createStatement().executeQuery("SELECT * FROM project_ops.flyway_schema_history"))
                    .hasMessageContaining("permission denied");
        }
    }

    @Test
    void theApplicationUsesTheBusinessAndMessagingSchemasOnly() throws Exception {
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT nspname, has_schema_privilege('project_app', oid, 'USAGE'), "
                                + "has_schema_privilege('project_app', oid, 'CREATE') FROM pg_namespace "
                                + "WHERE nspname LIKE 'project%' ORDER BY 1")) {
            java.util.List<String> schemas = new java.util.ArrayList<>();
            while (rs.next()) {
                schemas.add(rs.getString(1) + " usage=" + rs.getBoolean(2) + " create=" + rs.getBoolean(3));
            }
            assertThat(schemas)
                    .containsExactly(
                            "project usage=true create=false",
                            "project_messaging usage=true create=false",
                            "project_ops usage=false create=false");
        }
    }

    @Test
    void contractsAreInsertOnly() throws Exception {
        try (Connection c = db.asApp()) {
            UUID k = Rows.contract(c, Rows.project(c), 1);
            assertThatThrownBy(() -> Rows.exec(
                            c,
                            "UPDATE project.generation_contracts SET api_contract_version = '2' "
                                    + "WHERE generation_contract_id = ?",
                            k))
                    .hasMessageContaining("permission denied");
        }
    }

    @Test
    void theApplicationHoldsExactlyTheDocumentedPrivileges() throws Exception {
        String[] privileges = {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"};
        Map<String, TreeSet<String>> actual = new TreeMap<>();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT schemaname || '.' || tablename FROM pg_tables "
                                + "WHERE schemaname IN ('project', 'project_messaging', 'project_ops')")) {
            java.util.List<String> tables = new java.util.ArrayList<>();
            while (rs.next()) {
                tables.add(rs.getString(1));
            }
            for (String table : tables) {
                TreeSet<String> held = new TreeSet<>();
                for (String privilege : privileges) {
                    try (var ps = c.prepareStatement("SELECT has_table_privilege('project_app', ?, ?)")) {
                        ps.setString(1, table);
                        ps.setString(2, privilege);
                        try (ResultSet r = ps.executeQuery()) {
                            r.next();
                            if (r.getBoolean(1)) {
                                held.add(privilege);
                            }
                        }
                    }
                }
                actual.put(table, held);
            }
            // Column-level grants escape has_table_privilege. Only the three business tables of the column matrix
            // below may hold them (review L4: a GRANT UPDATE (business_profile) would otherwise go unnoticed).
            try (ResultSet columns = c.createStatement()
                    .executeQuery("SELECT DISTINCT table_schema || '.' || table_name || ' ' || privilege_type "
                            + "FROM information_schema.column_privileges WHERE grantee = 'project_app' "
                            + "AND table_schema IN ('project', 'project_messaging', 'project_ops') "
                            + "AND NOT has_table_privilege('project_app', "
                            + "quote_ident(table_schema) || '.' || quote_ident(table_name), privilege_type) "
                            + "AND (table_schema, table_name) NOT IN (('project', 'projects'), ('project', 'pages'), "
                            + "('project', 'generation_requests')) ORDER BY 1")) {
                java.util.List<String> columnOnly = new java.util.ArrayList<>();
                while (columns.next()) {
                    columnOnly.add(columns.getString(1));
                }
                assertThat(columnOnly)
                        .as("column-level grants outside the column matrix")
                        .isEmpty();
            }
        }
        // UPDATE is granted per column on these three tables; see the column matrix below.
        var rw = new TreeSet<>(java.util.List.of("INSERT", "SELECT"));
        var insertOnly = new TreeSet<>(java.util.List.of("INSERT", "SELECT"));
        var expected = new TreeMap<String, TreeSet<String>>(Map.of(
                "project.projects", rw,
                "project.pages", rw,
                "project.generation_requests", rw,
                "project.generation_contracts", insertOnly,
                "project.generation_contract_schemas", insertOnly,
                "project_messaging.processed_messages", insertOnly,
                "project_ops.flyway_schema_history", new TreeSet<String>()));
        // The outbox tables of V4 (spike S1) hold table-level UPDATE (the column-level rule covers the three business
        // tables only): records and partitions are read-write; instances are also deleted.
        var outboxReadWrite = new TreeSet<>(java.util.List.of("INSERT", "SELECT", "UPDATE"));
        expected.put("project_messaging.outbox_record", outboxReadWrite);
        expected.put("project_messaging.outbox_partition", outboxReadWrite);
        expected.put(
                "project_messaging.outbox_instance",
                new TreeSet<>(java.util.List.of("DELETE", "INSERT", "SELECT", "UPDATE")));
        // Decision 0013: the pending business profile of a catalog registration; deleted on success, never updated.
        expected.put(
                "project_messaging.pending_catalog_registrations",
                new TreeSet<>(java.util.List.of("DELETE", "INSERT", "SELECT")));
        assertThat(actual).containsExactlyInAnyOrderEntriesOf(expected);
    }

    private static final Map<String, java.util.Set<String>> FROZEN_COLUMNS = Map.of(
            "projects", java.util.Set.of("project_id", "owner_user_id", "subdomain", "business_type_id", "created_at"),
            "pages", java.util.Set.of("page_id", "project_id", "created_at"),
            "generation_requests",
                    java.util.Set.of(
                            "generation_request_id",
                            "project_id",
                            "idempotency_key",
                            "request_hash",
                            "generation_contract_id",
                            "captured_edit_revision",
                            "captured_input",
                            "captured_input_hash",
                            "created_at"));

    @Test
    void frozenColumnsAreNotUpdatableAndEveryOtherColumnIs() throws Exception {
        Map<String, String> wrong = new TreeMap<>();
        try (Connection c = db.asSuperuser()) {
            for (var entry : FROZEN_COLUMNS.entrySet()) {
                int seen = 0;
                try (var ps = c.prepareStatement("SELECT attname FROM pg_attribute "
                        + "WHERE attrelid = ('project.' || ?)::regclass AND attnum > 0 AND NOT attisdropped")) {
                    ps.setString(1, entry.getKey());
                    try (ResultSet rs = ps.executeQuery()) {
                        while (rs.next()) {
                            String column = rs.getString(1);
                            seen++;
                            boolean expected = !entry.getValue().contains(column);
                            try (var check = c.prepareStatement(
                                    "SELECT has_column_privilege('project_app', ('project.' || ?)::regclass, ?, 'UPDATE')")) {
                                check.setString(1, entry.getKey());
                                check.setString(2, column);
                                try (ResultSet r = check.executeQuery()) {
                                    r.next();
                                    if (r.getBoolean(1) != expected) {
                                        wrong.put(entry.getKey() + "." + column, "expected updatable=" + expected);
                                    }
                                }
                            }
                        }
                    }
                }
                assertThat(seen)
                        .as("columns of %s", entry.getKey())
                        .isGreaterThan(entry.getValue().size());
            }
        }
        assertThat(wrong).isEmpty();
    }

    /** Notion "2 · Project", "Who may write": exactly these eight columns of projects, nothing more. */
    @Test
    @DisplayName("FR-36.6: project_app may not update subdomain or business_type_id")
    void fr36_6_theApplicationMayNotUpdateTheSubdomainOrTheBusinessType() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            assertThatThrownBy(() ->
                            Rows.exec(c, "UPDATE project.projects SET subdomain = 'other' WHERE project_id = ?", p))
                    .isInstanceOf(PSQLException.class)
                    .hasMessageContaining("permission denied");
            assertThatThrownBy(() -> Rows.exec(
                            c,
                            "UPDATE project.projects SET business_type_id = ? WHERE project_id = ?",
                            UUID.randomUUID(),
                            p))
                    .isInstanceOf(PSQLException.class)
                    .hasMessageContaining("permission denied");
        }
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT string_agg(column_name, ',' ORDER BY column_name) "
                                + "FROM information_schema.column_privileges WHERE grantee = 'project_app' "
                                + "AND table_schema = 'project' AND table_name = 'projects' "
                                + "AND privilege_type = 'UPDATE'")) {
            rs.next();
            assertThat(rs.getString(1))
                    .isEqualTo("component_catalog_version,deleted_at,edit_revision,name,repository_full_name,"
                            + "repository_id,status,updated_at");
        }
    }

    /**
     * Notion "2 · Project": PUBLIC has no access. PostgreSQL gives PUBLIC EXECUTE on new functions and USAGE on new
     * types by default, including the row type it makes for every table; V6 revokes them on the guards and enums, V7
     * on the row type of every table in the three project schemas, so no grantee but the owner holds anything there.
     */
    @Test
    @DisplayName("TR-15 (FR-36): PUBLIC holds no privilege on the project schemas' functions, enums or table row types")
    void tr15_publicHoldsNothingOnProjectFunctionsOrTypes() throws Exception {
        java.util.List<String> rowTypes = new java.util.ArrayList<>();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT n.nspname || '.' || t.typname FROM pg_type t"
                                + " JOIN pg_namespace n ON n.oid = t.typnamespace"
                                + " WHERE n.nspname LIKE 'project%' AND t.typtype = 'c'")) {
            while (rs.next()) {
                rowTypes.add(rs.getString(1));
            }
        }
        assertThat(rowTypes)
                .containsExactlyInAnyOrder(
                        "project.generation_contract_schemas",
                        "project.generation_contracts",
                        "project.generation_requests",
                        "project.pages",
                        "project.projects",
                        "project_messaging.outbox_instance",
                        "project_messaging.outbox_partition",
                        "project_messaging.outbox_record",
                        "project_messaging.pending_catalog_registrations",
                        "project_messaging.processed_messages",
                        "project_ops.flyway_schema_history");
        java.util.List<String> held = new java.util.ArrayList<>();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery(
                                "SELECT 'function ' || p.proname || ' ' || a.privilege_type || ' '"
                                        + " || coalesce(nullif(a.grantee::regrole::text, '-'), 'PUBLIC')"
                                        + " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                                        + " CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a"
                                        + " WHERE n.nspname LIKE 'project%' AND a.grantee <> p.proowner"
                                        + " UNION ALL SELECT 'type ' || t.typname || ' ' || a.privilege_type || ' '"
                                        + " || coalesce(nullif(a.grantee::regrole::text, '-'), 'PUBLIC')"
                                        + " FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
                                        + " CROSS JOIN LATERAL aclexplode(coalesce(t.typacl, acldefault('T', t.typowner))) a"
                                        + " WHERE n.nspname LIKE 'project%' AND t.typtype IN ('e', 'c') AND a.grantee <> t.typowner")) {
            while (rs.next()) {
                held.add(rs.getString(1));
            }
        }
        assertThat(held).isEmpty();
    }

    @Test
    void theOwnerOfAProjectCannotBeReassigned() throws Exception {
        try (Connection c = db.asApp()) {
            UUID p = Rows.project(c);
            assertThatThrownBy(() -> Rows.exec(
                            c,
                            "UPDATE project.projects SET owner_user_id = ? WHERE project_id = ?",
                            UUID.randomUUID(),
                            p))
                    .isInstanceOf(PSQLException.class)
                    .hasMessageContaining("permission denied");
        }
    }
}
