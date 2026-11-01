package org.kshrd.webblock.project.common.domain.error;

/**
 * Compile-time constants for Bean Validation {@code message} attributes, which cannot reference enum values.
 * Each constant equals the name of a field-level {@link ErrorCode}; ErrorCodeCatalogTest keeps the two in step.
 */
public final class ValidationCodes {

    public static final String FIELD_REQUIRED = "FIELD_REQUIRED";
    public static final String FIELD_LENGTH_INVALID = "FIELD_LENGTH_INVALID";
    public static final String FIELD_OUT_OF_RANGE = "FIELD_OUT_OF_RANGE";
    public static final String VALUE_INVALID = "VALUE_INVALID";
    public static final String UUID_INVALID = "UUID_INVALID";
    public static final String PAGE_PATH_INVALID = "PAGE_PATH_INVALID";
    public static final String IDEMPOTENCY_KEY_INVALID = "IDEMPOTENCY_KEY_INVALID";
    public static final String PUCK_TREE_INVALID = "PUCK_TREE_INVALID";

    private ValidationCodes() {}
}
