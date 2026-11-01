package org.kshrd.webblock.project.application.generation.model;

import com.fasterxml.jackson.annotation.JsonRawValue;
import java.util.List;
import java.util.UUID;
import tools.jackson.databind.JsonNode;

/**
 * What AI Codegen reads for a generating request (communication note 2.2). There is no scaffold and no limits here:
 * both belong to J4.
 */
public record CodegenInput(
        UUID generationRequestId,
        UUID projectId,
        String businessTypeCode,
        @JsonRawValue String capturedInput,
        String capturedInputHash,
        Contract generationContract) {

    /** The frozen contract: one entry per product type, in ascending productTypeId. */
    public record Contract(UUID generationContractId, String apiContractVersion, List<ProductType> productTypes) {}

    public record ProductType(UUID productTypeId, String code, UUID productTypeSchemaId, JsonNode fieldSchema) {}
}
