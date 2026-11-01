package org.kshrd.webblock.project.application.catalog.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

@Tag("integration")
class CatalogProductTypesTest {

    static final String DEAD_URL = "jdbc:postgresql://127.0.0.1:1/none?connectTimeout=1";
    static final UUID A = UUID.randomUUID();
    static final UUID B = UUID.randomUUID();
    static final UUID T1 = UUID.randomUUID();
    static final UUID T2 = UUID.randomUUID();
    static final UUID T3 = UUID.randomUUID();
    static final UUID T4 = UUID.randomUUID();
    static final UUID T1V1 = UUID.randomUUID();
    static final UUID T1V2 = UUID.randomUUID();
    static final UUID T1V3 = UUID.randomUUID();
    static final UUID T2V1 = UUID.randomUUID();
    static final UUID T3V1 = UUID.randomUUID();
    static final UUID T4V1 = UUID.randomUUID();
    static final String A_CODE = "bta_" + UUID.randomUUID().toString().substring(0, 8);

    static PostgresFixture.CatalogDatabase db;
    static CatalogReference reference;

    @BeforeAll
    static void rows() throws Exception {
        db = PostgresFixture.freshCatalogDatabase(true);
        try (Connection owner = db.asOwner()) {
            businessType(owner, A, A_CODE, true);
            businessType(owner, B, "btb_" + UUID.randomUUID().toString().substring(0, 8), true);
            type(owner, T1, A, "t1code", true);
            type(owner, T2, A, "t2code", false);
            type(owner, T3, A, "t3code", true);
            type(owner, T4, B, "t4code", true);
            schema(owner, T1V1, T1, 1, true);
            schema(owner, T1V2, T1, 2, true);
            schema(owner, T1V3, T1, 3, false);
            schema(owner, T2V1, T2, 1, true);
            schema(owner, T3V1, T3, 1, false);
            schema(owner, T4V1, T4, 1, true);
        }
        reference = new CatalogReference(JdbcClient.create(new DriverManagerDataSource(
                db.url(), PostgresFixture.CATALOG_PLATFORM_LOGIN, PostgresFixture.PASSWORD)));
    }

    private static void businessType(Connection c, UUID id, String code, boolean active) throws Exception {
        try (PreparedStatement ps =
                c.prepareStatement("INSERT INTO catalog.business_types VALUES (?, ?, ?, ?, now())")) {
            ps.setObject(1, id);
            ps.setString(2, code);
            ps.setString(3, code + " name");
            ps.setBoolean(4, active);
            ps.executeUpdate();
        }
    }

    private static void type(Connection c, UUID id, UUID businessTypeId, String code, boolean active) throws Exception {
        try (PreparedStatement ps =
                c.prepareStatement("INSERT INTO catalog.product_types VALUES (?, ?, ?, ?, ?, now())")) {
            ps.setObject(1, id);
            ps.setObject(2, businessTypeId);
            ps.setString(3, code);
            ps.setString(4, code + " name");
            ps.setBoolean(5, active);
            ps.executeUpdate();
        }
    }

    private static void schema(Connection c, UUID id, UUID typeId, int version, boolean active) throws Exception {
        try (PreparedStatement ps = c.prepareStatement(
                "INSERT INTO catalog.product_type_schemas VALUES (?, ?, ?, '{\"type\":\"object\"}'::jsonb, ?, now())")) {
            ps.setObject(1, id);
            ps.setObject(2, typeId);
            ps.setInt(3, version);
            ps.setBoolean(4, active);
            ps.executeUpdate();
        }
    }

    private static CatalogReference unreachable() {
        return new CatalogReference(JdbcClient.create(new DriverManagerDataSource(
                DEAD_URL, PostgresFixture.CATALOG_PLATFORM_LOGIN, PostgresFixture.PASSWORD)));
    }

    @Test
    void picksTheHighestActiveSchemaOfEachAllowedType() {
        var found = reference.activeProductTypeSchemas(A, List.of(T1, T2, T3, T4, UUID.randomUUID()));
        assertThat(found).containsOnlyKeys(T1);
        assertThat(found.get(T1).versionNumber()).isEqualTo(2);
        assertThat(found.get(T1).productTypeSchemaId()).isEqualTo(T1V2);
        assertThat(found.get(T1).fieldSchema().isObject()).isTrue();
        assertThat(found.get(T1).code()).isEqualTo("t1code");
    }

    @Test
    void severalTypesAcrossSeveralIdsBindAsAnArray() {
        assertThat(reference.activeProductTypeSchemas(B, List.of(T4, T1))).containsOnlyKeys(T4);
    }

    @Test
    void frozenSchemasAreReadByIdEvenWhenInactive() {
        var found = reference.productTypeSchemas(List.of(T1V3, T2V1));
        assertThat(found).containsOnlyKeys(T1V3, T2V1);
        assertThat(found.get(T1V3).versionNumber()).isEqualTo(3);
        assertThat(found.get(T1V3).productTypeId()).isEqualTo(T1);
        assertThat(found.get(T2V1).versionNumber()).isEqualTo(1);
        assertThat(found.get(T2V1).code()).isEqualTo("t2code");
    }

    @Test
    void readsABusinessTypeById() {
        assertThat(reference.businessType(A)).get().extracting(b -> b.code()).isEqualTo(A_CODE);
        assertThat(reference.businessType(UUID.randomUUID())).isEmpty();
    }

    @Test
    void anEmptyRequestQueriesNothing() {
        var offline = unreachable();
        assertThat(offline.activeProductTypeSchemas(A, List.of())).isEmpty();
        assertThat(offline.productTypeSchemas(List.of())).isEmpty();
    }

    @Test
    void anUnreachableCatalogIsCatalogUnavailable() {
        var offline = unreachable();
        for (Runnable call : List.<Runnable>of(
                () -> offline.activeProductTypeSchemas(A, List.of(T1)),
                () -> offline.productTypeSchemas(List.of(T1V1)),
                () -> offline.businessType(A))) {
            assertThatThrownBy(call::run)
                    .isInstanceOfSatisfying(
                            WebBlockException.class,
                            e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.CATALOG_UNAVAILABLE));
        }
    }
}
