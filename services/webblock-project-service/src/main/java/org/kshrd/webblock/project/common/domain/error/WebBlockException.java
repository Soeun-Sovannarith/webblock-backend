package org.kshrd.webblock.project.common.domain.error;

import java.util.List;
import java.util.Objects;

/** A failure the service reports to the caller with a stable {@link ErrorCode}. */
public class WebBlockException extends RuntimeException {

    private final ErrorCode errorCode;
    private final List<ErrorDetail> details;

    public WebBlockException(ErrorCode errorCode) {
        this(errorCode, List.of(), null);
    }

    public WebBlockException(ErrorCode errorCode, List<ErrorDetail> details) {
        this(errorCode, details, null);
    }

    public WebBlockException(ErrorCode errorCode, Throwable cause) {
        this(errorCode, List.of(), cause);
    }

    private WebBlockException(ErrorCode errorCode, List<ErrorDetail> details, Throwable cause) {
        super(Objects.requireNonNull(errorCode, "errorCode").code(), cause);
        this.errorCode = errorCode;
        this.details = List.copyOf(details);
    }

    public ErrorCode errorCode() {
        return errorCode;
    }

    public List<ErrorDetail> details() {
        return details;
    }
}
