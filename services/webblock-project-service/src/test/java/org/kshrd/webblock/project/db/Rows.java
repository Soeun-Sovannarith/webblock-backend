package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.fail;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.UUID;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.postgresql.util.PSQLException;

/** Valid rows for the project schema, and an assertion on the violated constraint's name. */
final class Rows {

    private Rows() {}

    @FunctionalInterface
    interface SqlAction {
        void run() throws SQLException;
    }

    static int exec(Connection c, String sql, Object... params) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            for (int i = 0; i < params.length; i++) {
                ps.setObject(i + 1, params[i]);
            }
            return ps.executeUpdate();
        }
    }

    static void assertViolates(String constraint, SqlAction action) {
        try {
            action.run();
            fail("Expected a violation of " + constraint);
        } catch (PSQLException e) {
            assertThat(e.getServerErrorMessage()).isNotNull();
            assertThat(e.getServerErrorMessage().getConstraint()).isEqualTo(constraint);
        } catch (SQLException e) {
            fail("Expected a PSQLException naming " + constraint + " but got " + e);
        }
    }

    /** A project with a sub-domain no other row holds ({@link TestSubdomains#of}). */
    static UUID project(Connection c) throws SQLException {
        UUID id = UUID.randomUUID();
        insertProject(c, id, TestSubdomains.of(id));
        return id;
    }

    static UUID project(Connection c, String subdomain) throws SQLException {
        UUID id = UUID.randomUUID();
        insertProject(c, id, subdomain);
        return id;
    }

    private static void insertProject(Connection c, UUID id, String subdomain) throws SQLException {
        exec(
                c,
                "INSERT INTO project.projects (project_id, owner_user_id, name, subdomain, business_type_id, "
                        + "component_catalog_version, status, created_at, updated_at) "
                        + "VALUES (?, ?, 'Shop', ?, ?, '1', 'draft', now(), now())",
                id,
                UUID.randomUUID(),
                subdomain,
                UUID.randomUUID());
    }

    static UUID page(Connection c, UUID projectId, String path) throws SQLException {
        UUID id = UUID.randomUUID();
        exec(
                c,
                "INSERT INTO project.pages (page_id, project_id, name, path, puck_tree, sort_order, created_at, "
                        + "updated_at) VALUES (?, ?, 'Home', ?, '{\"content\":[]}'::jsonb, 0, now(), now())",
                id,
                projectId,
                path);
        return id;
    }

    static UUID contract(Connection c, UUID projectId, int version) throws SQLException {
        UUID id = UUID.randomUUID();
        exec(
                c,
                "INSERT INTO project.generation_contracts (generation_contract_id, project_id, version_number, "
                        + "api_contract_version, created_by_user_id, created_at) VALUES (?, ?, ?, '1', ?, now())",
                id,
                projectId,
                version,
                UUID.randomUUID());
        return id;
    }

    static UUID request(Connection c, UUID projectId, UUID contractId, String key, String status) throws SQLException {
        UUID id = UUID.randomUUID();
        exec(
                c,
                "INSERT INTO project.generation_requests (generation_request_id, project_id, idempotency_key, "
                        + "request_hash, generation_contract_id, captured_edit_revision, captured_input, "
                        + "captured_input_hash, status, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, ?, 0, '{}'::jsonb, ?, ?::project.generation_request_status, now(), now())",
                id,
                projectId,
                key,
                "a".repeat(64),
                contractId,
                "b".repeat(64),
                status);
        return id;
    }

    static void contractSchema(Connection c, UUID contractId, UUID schemaId, UUID productTypeId) throws SQLException {
        exec(
                c,
                "INSERT INTO project.generation_contract_schemas (generation_contract_id, product_type_schema_id, "
                        + "product_type_id) VALUES (?, ?, ?)",
                contractId,
                schemaId,
                productTypeId);
    }
}
