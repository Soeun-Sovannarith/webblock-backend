package org.kshrd.webblock.project.application.generation.model;

import java.util.UUID;

/** One product type a contract froze, with its frozen schema's version_number in the catalog. */
public record ContractProductType(UUID productTypeId, int schemaVersion) {}
