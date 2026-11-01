package org.kshrd.webblock.project.common.domain.support;

import java.util.regex.Pattern;

/**
 * The names under which a request's ID travels (HTTP header, servlet request attribute and logging MDC key), and the
 * MDC key under which Micrometer Tracing puts the trace ID.
 */
public final class RequestIds {

    public static final String HEADER = "X-Request-Id";
    public static final String ATTRIBUTE = "org.kshrd.webblock.project.requestId";
    public static final String MDC_KEY = "requestId";
    public static final String TRACE_ID_MDC_KEY = "traceId";

    private static final Pattern SAFE = Pattern.compile("^[A-Za-z0-9._-]{1,128}$");

    private RequestIds() {}

    /**
     * The caller's request ID (REST header or gRPC metadata) when it is 1 to 128 letters, digits, dots, underscores or
     * dashes; anything else, or none, gets a new UUIDv7. A kept ID is safe to log and to send back.
     */
    public static String acceptedOrNew(String incoming) {
        return incoming != null && SAFE.matcher(incoming).matches()
                ? incoming
                : Uuids.v7().toString();
    }
}
