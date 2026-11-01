package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;

import java.sql.Connection;
import java.sql.ResultSet;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

@Tag("integration")
class ContractSchemasTest {

    private static PostgresFixture.Database db;

    @BeforeAll
    static void migrate() throws Exception {
        db = PostgresFixture.freshDatabase();
    }

    @Test
    void aSchemaIsSelectedOncePerContract() throws Exception {
        try (Connection c = db.asApp()) {
            UUID k = Rows.contract(c, Rows.project(c), 1);
            UUID schema = UUID.randomUUID();
            Rows.contractSchema(c, k, schema, UUID.randomUUID());
            Rows.assertViolates(
                    "generation_contract_schemas_pkey", () -> Rows.contractSchema(c, k, schema, UUID.randomUUID()));
            try (var ps = c.prepareStatement(
                    "SELECT count(*) FROM project.generation_contract_schemas WHERE generation_contract_id = ?")) {
                ps.setObject(1, k);
                try (ResultSet rs = ps.executeQuery()) {
                    rs.next();
                    assertThat(rs.getInt(1)).isEqualTo(1);
                }
            }
        }
    }

    @Test
    void oneSchemaVersionPerProductType() throws Exception {
        try (Connection c = db.asApp()) {
            UUID k = Rows.contract(c, Rows.project(c), 1);
            UUID type = UUID.randomUUID();
            Rows.contractSchema(c, k, UUID.randomUUID(), type);
            Rows.assertViolates(
                    "generation_contract_schemas_type_key", () -> Rows.contractSchema(c, k, UUID.randomUUID(), type));
        }
    }

    @Test
    void aSchemaBelongsToAnExistingContract() throws Exception {
        try (Connection c = db.asApp()) {
            Rows.assertViolates(
                    "generation_contract_schemas_contract_fk",
                    () -> Rows.contractSchema(c, UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID()));
        }
    }
}
