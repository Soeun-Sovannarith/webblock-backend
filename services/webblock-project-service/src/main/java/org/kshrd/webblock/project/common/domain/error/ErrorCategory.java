package org.kshrd.webblock.project.common.domain.error;

/** The kind of an error; decides the HTTP status. */
public enum ErrorCategory {
    VALIDATION(400),
    UNAUTHENTICATED(401),
    FORBIDDEN(403),
    NOT_FOUND(404),
    METHOD_NOT_ALLOWED(405),
    NOT_ACCEPTABLE(406),
    CONFLICT(409),
    PAYLOAD_TOO_LARGE(413),
    UNSUPPORTED_MEDIA_TYPE(415),
    BUSINESS_RULE(422),
    INTERNAL(500),
    DEPENDENCY(503);

    private final int httpStatus;

    ErrorCategory(int httpStatus) {
        this.httpStatus = httpStatus;
    }

    public int httpStatus() {
        return httpStatus;
    }
}
