package org.kshrd.webblock.project.application.catalog.model;

import tools.jackson.databind.JsonNode;

/** An active product type with the field schema of its latest active version. */
public record ActiveProductType(String code, String name, JsonNode fieldSchema) {}
