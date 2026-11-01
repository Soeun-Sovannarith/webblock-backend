package org.kshrd.webblock.project.common.presentation.envelope;

/**
 * The shape of every successful JSON response under /api and /internal. Errors are Problem Details bodies (spec
 * section 6.3), never this record.
 */
public record ApiResponse<T>(boolean success, T data, ApiMeta meta) {

    public static <T> ApiResponse<T> success(T data, ApiMeta meta) {
        return new ApiResponse<>(true, data, meta);
    }
}
