package org.kshrd.webblock.project.application.generation.handler;

import java.util.Comparator;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;

/** The two rules that contract reads and codegen input share about a contract's frozen product type schemas. */
final class FrozenSchemas {

    private FrozenSchemas() {}

    /**
     * Product types in ascending productTypeId (lead ruling C2), compared as their canonical text: PostgreSQL's uuid
     * order. UUID.compareTo compares signed halves and would put 8…–f… before 0…–7….
     */
    static <T> Comparator<T> byProductTypeId(Function<T, UUID> productTypeId) {
        return Comparator.comparing(type -> productTypeId.apply(type).toString());
    }

    /**
     * @throws IllegalStateException the frozen schema is not in the catalog: a broken invariant, answered as a 500
     */
    static ProductTypeSchema require(Map<UUID, ProductTypeSchema> schemas, UUID productTypeSchemaId) {
        ProductTypeSchema schema = schemas.get(productTypeSchemaId);
        if (schema == null) {
            throw new IllegalStateException(
                    "Frozen product type schema " + productTypeSchemaId + " is not in the catalog");
        }
        return schema;
    }
}
