package org.kshrd.webblock.project.application.catalog.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

@Tag("integration")
class CatalogReferenceTest {

    static PostgresFixture.CatalogDatabase db;
    static final String ACTIVE = "ref_" + UUID.randomUUID().toString().substring(0, 8) + "_shop";
    static final String INACTIVE = "old_" + UUID.randomUUID().toString().substring(0, 8) + "_shop";

    static final UUID WEBSITE = UUID.randomUUID();

    @BeforeAll
    static void rows() throws Exception {
        db = PostgresFixture.freshCatalogDatabase(true);
        UUID activeId = UUID.randomUUID();
        try (Connection owner = db.asOwner();
                PreparedStatement ps =
                        owner.prepareStatement("INSERT INTO catalog.business_types VALUES (?, ?, ?, ?, now())")) {
            for (Object[] row : new Object[][] {
                {activeId, ACTIVE, "Active shop", true}, {UUID.randomUUID(), INACTIVE, "Old shop", false}
            }) {
                ps.setObject(1, row[0]);
                ps.setString(2, (String) row[1]);
                ps.setString(3, (String) row[2]);
                ps.setBoolean(4, (Boolean) row[3]);
                ps.addBatch();
            }
            ps.executeBatch();
        }
        try (Connection owner = db.asOwner();
                PreparedStatement ps = owner.prepareStatement(
                        "INSERT INTO catalog.websites VALUES (?, ?, ?, 'active', 1, 1, 0, now(), now())")) {
            ps.setObject(1, WEBSITE);
            ps.setObject(2, activeId);
            ps.setString(3, "site_" + WEBSITE.toString().replace("-", ""));
            ps.executeUpdate();
        }
    }

    private static CatalogReference reference(String url) {
        return new CatalogReference(JdbcClient.create(
                new DriverManagerDataSource(url, PostgresFixture.CATALOG_PLATFORM_LOGIN, PostgresFixture.PASSWORD)));
    }

    @Test
    void listsActiveTypesAsCatalogPlatform() {
        assertThat(reference(db.url()).activeBusinessTypes())
                .extracting(BusinessType::code)
                .contains(ACTIVE)
                .doesNotContain(INACTIVE);
    }

    @Test
    void findsByCodeWithAndWithoutTheActiveFilter() {
        CatalogReference reference = reference(db.url());
        assertThat(reference.activeBusinessType(ACTIVE))
                .get()
                .extracting(BusinessType::name)
                .isEqualTo("Active shop");
        assertThat(reference.activeBusinessType(INACTIVE)).isEmpty();
        assertThat(reference.businessType(INACTIVE))
                .get()
                .extracting(BusinessType::active)
                .isEqualTo(false);
        assertThat(reference.businessType("missing_shop")).isEmpty();
    }

    @Test
    void seesAnyWebsiteAsCatalogPlatform() {
        CatalogReference reference = reference(db.url());
        assertThat(reference.websiteExists(WEBSITE)).isTrue();
        assertThat(reference.websiteExists(UUID.randomUUID())).isFalse();
    }

    @Test
    void anUnreachableCatalogIsADependencyErrorForTheWebsiteCheckToo() {
        assertThatThrownBy(() -> reference("jdbc:postgresql://127.0.0.1:1/none?connectTimeout=1")
                        .websiteExists(WEBSITE))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.CATALOG_UNAVAILABLE));
    }

    @Test
    @DisplayName("FR-14.2: business types by ID come in one query; an ID with no row is absent; none asks nothing")
    void fr14_2_businessTypesByIdInOneQuery() {
        CatalogReference reference = reference(db.url());
        UUID active = reference.businessType(ACTIVE).orElseThrow().businessTypeId();
        UUID inactive = reference.businessType(INACTIVE).orElseThrow().businessTypeId();
        assertThat(reference.businessTypes(List.of(active, inactive, UUID.randomUUID())))
                .containsOnlyKeys(active, inactive)
                .extractingByKey(active)
                .extracting(BusinessType::name)
                .isEqualTo("Active shop");
        assertThat(reference.businessTypes(List.of())).isEmpty();
        assertThatThrownBy(() -> reference("jdbc:postgresql://127.0.0.1:1/none?connectTimeout=1")
                        .businessTypes(List.of(active)))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.CATALOG_UNAVAILABLE));
    }

    @Test
    void anUnreachableCatalogIsADependencyError() {
        assertThatThrownBy(() -> reference("jdbc:postgresql://127.0.0.1:1/none?connectTimeout=1")
                        .activeBusinessTypes())
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.CATALOG_UNAVAILABLE));
    }
}
