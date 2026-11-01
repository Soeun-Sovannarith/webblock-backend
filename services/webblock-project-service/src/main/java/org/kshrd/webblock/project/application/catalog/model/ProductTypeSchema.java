package org.kshrd.webblock.project.application.catalog.model;

import java.util.UUID;
import tools.jackson.databind.JsonNode;

/** One version of a product type's field schema, with the type's identity. */
public record ProductTypeSchema(
        UUID productTypeId, String code, UUID productTypeSchemaId, int versionNumber, JsonNode fieldSchema) {}
