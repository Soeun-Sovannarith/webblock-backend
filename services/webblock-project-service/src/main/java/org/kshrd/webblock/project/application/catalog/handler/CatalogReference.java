package org.kshrd.webblock.project.application.catalog.handler;

import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Supplier;
import org.kshrd.webblock.project.application.catalog.model.ActiveProductType;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.DependsOn;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;

/**
 * Reads the catalog as catalog_platform (SELECT on business_types and websites, V4;
 * product_types and product_type_schemas, V5), each read bounded by the catalog
 * pool's statement timeout (CatalogDataSourceConfig). Ordered after the catalog migrations, so a fresh non-production
 * database is migrated and seeded before the first read.
 */
@Service
@DependsOn("catalogMigrations")
public class CatalogReference {

    private static final RowMapper<BusinessType> ROW = (rs, n) -> new BusinessType(
            rs.getObject("business_type_id", UUID.class),
            rs.getString("code"),
            rs.getString("name"),
            rs.getBoolean("is_active"));
    private static final String COLUMNS = "SELECT business_type_id, code, name, is_active FROM catalog.business_types ";

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final RowMapper<ProductTypeSchema> SCHEMA_ROW = (rs, n) -> new ProductTypeSchema(
            rs.getObject("product_type_id", UUID.class),
            rs.getString("code"),
            rs.getObject("product_type_schema_id", UUID.class),
            rs.getInt("version_number"),
            JSON.readTree(rs.getString("field_schema")));
    private static final String SCHEMA_COLUMNS = "SELECT t.product_type_id, t.code, s.product_type_schema_id, "
            + "s.version_number, s.field_schema::text AS field_schema FROM catalog.product_types t "
            + "JOIN catalog.product_type_schemas s ON s.product_type_id = t.product_type_id ";

    private final JdbcClient catalog;

    public CatalogReference(@Qualifier("catalog") JdbcClient catalog) {
        this.catalog = catalog;
    }

    public List<BusinessType> activeBusinessTypes() {
        return read(() -> catalog.sql(COLUMNS + "WHERE is_active ORDER BY name, code")
                .query(ROW)
                .list());
    }

    public Optional<BusinessType> activeBusinessType(String code) {
        return businessType(code).filter(BusinessType::active);
    }

    public Optional<BusinessType> businessType(String code) {
        return read(() ->
                catalog.sql(COLUMNS + "WHERE code = ?").param(code).query(ROW).optional());
    }

    public Optional<BusinessType> businessType(UUID businessTypeId) {
        return read(() -> catalog.sql(COLUMNS + "WHERE business_type_id = ?")
                .param(businessTypeId)
                .query(ROW)
                .optional());
    }

    /** The business types with these IDs in one query, keyed by ID; an ID with no row is absent. */
    public Map<UUID, BusinessType> businessTypes(Collection<UUID> businessTypeIds) {
        if (businessTypeIds.isEmpty()) {
            return Map.of();
        }
        List<BusinessType> rows = read(() -> catalog.sql(COLUMNS + "WHERE business_type_id = ANY (?)")
                .param(businessTypeIds.toArray(UUID[]::new))
                .query(ROW)
                .list());
        Map<UUID, BusinessType> result = new LinkedHashMap<>();
        rows.forEach(row -> result.put(row.businessTypeId(), row));
        return result;
    }

    /**
     * For each requested product type that belongs to the business type and is active, its active schema with the
     * highest version number, keyed by product type ID. A type that fails any of these is absent.
     */
    public Map<UUID, ProductTypeSchema> activeProductTypeSchemas(UUID businessTypeId, Collection<UUID> productTypeIds) {
        if (productTypeIds.isEmpty()) {
            return Map.of();
        }
        return read(() -> byKey(
                catalog.sql("SELECT DISTINCT ON (t.product_type_id) t.product_type_id, t.code, "
                                + "s.product_type_schema_id, s.version_number, s.field_schema::text AS field_schema "
                                + "FROM catalog.product_types t "
                                + "JOIN catalog.product_type_schemas s ON s.product_type_id = t.product_type_id "
                                + "WHERE t.business_type_id = ? AND t.is_active AND s.is_active "
                                + "AND t.product_type_id = ANY (?) "
                                + "ORDER BY t.product_type_id, s.version_number DESC")
                        .param(businessTypeId)
                        .param(productTypeIds.toArray(UUID[]::new))
                        .query(SCHEMA_ROW)
                        .list(),
                false));
    }

    /**
     * Every active product type of the business type that has an active schema, with that schema of the highest
     * version number, keyed by product type ID.
     */
    public Map<UUID, ProductTypeSchema> allActiveProductTypeSchemas(UUID businessTypeId) {
        return read(() -> byKey(
                catalog.sql("SELECT DISTINCT ON (t.product_type_id) t.product_type_id, t.code, "
                                + "s.product_type_schema_id, s.version_number, s.field_schema::text AS field_schema "
                                + "FROM catalog.product_types t "
                                + "JOIN catalog.product_type_schemas s ON s.product_type_id = t.product_type_id "
                                + "WHERE t.business_type_id = ? AND t.is_active AND s.is_active "
                                + "ORDER BY t.product_type_id, s.version_number DESC")
                        .param(businessTypeId)
                        .query(SCHEMA_ROW)
                        .list(),
                false));
    }

    /**
     * Every active product type of the business type with its active schema of the highest version number, ordered
     * by product type name. A product type without an active schema is absent.
     */
    public List<ActiveProductType> activeProductTypes(UUID businessTypeId) {
        return read(() -> catalog.sql("SELECT code, name, field_schema FROM ("
                        + "SELECT DISTINCT ON (t.product_type_id) t.code, t.name, "
                        + "s.field_schema::text AS field_schema FROM catalog.product_types t "
                        + "JOIN catalog.product_type_schemas s ON s.product_type_id = t.product_type_id "
                        + "WHERE t.business_type_id = ? AND t.is_active AND s.is_active "
                        + "ORDER BY t.product_type_id, s.version_number DESC) latest ORDER BY name, code")
                .param(businessTypeId)
                .query((rs, n) -> new ActiveProductType(
                        rs.getString("code"), rs.getString("name"), JSON.readTree(rs.getString("field_schema"))))
                .list());
    }

    /** Frozen schemas by their own IDs, whatever their or their type's {@code is_active} now. */
    public Map<UUID, ProductTypeSchema> productTypeSchemas(Collection<UUID> productTypeSchemaIds) {
        if (productTypeSchemaIds.isEmpty()) {
            return Map.of();
        }
        return read(() -> byKey(
                catalog.sql(SCHEMA_COLUMNS + "WHERE s.product_type_schema_id = ANY (?)")
                        .param(productTypeSchemaIds.toArray(UUID[]::new))
                        .query(SCHEMA_ROW)
                        .list(),
                true));
    }

    private static Map<UUID, ProductTypeSchema> byKey(List<ProductTypeSchema> rows, boolean bySchemaId) {
        Map<UUID, ProductTypeSchema> result = new LinkedHashMap<>();
        for (ProductTypeSchema row : rows) {
            result.put(bySchemaId ? row.productTypeSchemaId() : row.productTypeId(), row);
        }
        return result;
    }

    /** Whether the catalog holds a website with this ID, whoever registered it. */
    public boolean websiteExists(UUID websiteId) {
        return read(() -> catalog.sql("SELECT EXISTS (SELECT 1 FROM catalog.websites WHERE website_id = ?)")
                .param(websiteId)
                .query(Boolean.class)
                .single());
    }

    private static <T> T read(Supplier<T> query) {
        try {
            return query.get();
        } catch (DataAccessException e) {
            throw new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE, e);
        }
    }
}
