package org.kshrd.webblock.project.common.presentation.error;

import org.kshrd.webblock.project.common.domain.error.ErrorCode;

/** Maps a bare HTTP status, from a framework exception or the servlet container, to the catalogue code. */
final class StatusErrorCodes {

    private StatusErrorCodes() {}

    static ErrorCode forStatus(int status) {
        if (status >= 500) {
            return ErrorCode.INTERNAL_ERROR;
        }
        return switch (status) {
            case 401 -> ErrorCode.UNAUTHENTICATED;
            case 403 -> ErrorCode.ACCESS_DENIED;
            case 404 -> ErrorCode.ROUTE_NOT_FOUND;
            case 405 -> ErrorCode.METHOD_NOT_ALLOWED;
            case 413 -> ErrorCode.PAYLOAD_TOO_LARGE;
            case 406 -> ErrorCode.NOT_ACCEPTABLE;
            case 415 -> ErrorCode.UNSUPPORTED_MEDIA_TYPE;
            // Any other 4xx a framework exception reports stays a generic 400.
            default -> ErrorCode.MALFORMED_REQUEST;
        };
    }
}
