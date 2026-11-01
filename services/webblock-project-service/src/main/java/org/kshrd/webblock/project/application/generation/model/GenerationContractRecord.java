package org.kshrd.webblock.project.application.generation.model;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * A generation contract as the owner sees it; also the JSON body of the 201 and of each list item (J2-15).
 * productTypes is sorted by productTypeId ascending.
 */
public record GenerationContractRecord(
        UUID generationContractId,
        int versionNumber,
        String apiContractVersion,
        List<ContractProductType> productTypes,
        UUID createdByUserId,
        Instant createdAt) {

    public GenerationContractRecord {
        productTypes = List.copyOf(productTypes);
    }
}
