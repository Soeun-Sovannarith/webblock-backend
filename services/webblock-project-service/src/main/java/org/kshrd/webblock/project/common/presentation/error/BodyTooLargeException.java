package org.kshrd.webblock.project.common.presentation.error;

import java.io.IOException;
import org.kshrd.webblock.project.common.presentation.filter.RequestBodyLimitFilter;

/** Thrown by the counting request stream of {@link RequestBodyLimitFilter} when a body outgrows the limit. */
public class BodyTooLargeException extends IOException {

    public BodyTooLargeException(long limit) {
        super("The request body is larger than " + limit + " bytes");
    }
}
