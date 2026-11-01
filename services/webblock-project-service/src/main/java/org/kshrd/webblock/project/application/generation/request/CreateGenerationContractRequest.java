package org.kshrd.webblock.project.application.generation.request;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import java.util.List;
import java.util.UUID;
import org.hibernate.validator.constraints.UniqueElements;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/** At least one product type, no duplicates; the client never sends apiContractVersion (J2-3). */
public record CreateGenerationContractRequest(
        @NotEmpty(message = ValidationCodes.FIELD_REQUIRED) @UniqueElements(message = ValidationCodes.VALUE_INVALID)
        List<@NotNull(message = ValidationCodes.FIELD_REQUIRED) UUID> productTypeIds) {}
