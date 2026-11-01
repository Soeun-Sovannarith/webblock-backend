package org.kshrd.webblock.project.common.domain.error;

/** One problem with one field of a request. Never carries the rejected value. */
public record ErrorDetail(String field, String code, String message) {

    public static ErrorDetail of(String field, ErrorCode errorCode) {
        return new ErrorDetail(field, errorCode.code(), errorCode.message());
    }
}
