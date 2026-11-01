package org.kshrd.webblock.notification.common.presentation;

import org.slf4j.MDC;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Continues the trace of the request or message being handled (observability.md). It keeps the incoming W3C
 * {@code traceparent} and the {@code x-request-id} for the current thread, and mirrors them into the log context as
 * {@code trace_id}, {@code span_id} and {@code request_id}, so one log line leads to its trace.
 * <p>
 * Both header values come from outside and are untrusted. They are accepted only in their exact format, which also keeps
 * line breaks and quotes out of the logs. A missing or malformed {@code traceparent} is dropped (no trace is invented);
 * a missing or malformed request id is replaced by a new one, so every log line has a request id.
 * <p>
 * No tracing library creates spans in this service: the incoming trace is continued, and passed on unchanged.
 */
public final class TraceContext {

    public static final String TRACEPARENT_HEADER = "traceparent";
    public static final String REQUEST_ID_HEADER = "x-request-id";

    public static final String TRACE_ID = "trace_id";
    public static final String SPAN_ID = "span_id";
    public static final String REQUEST_ID = "request_id";

    /** version-traceid-parentid-flags, lower-case hex (W3C Trace Context). */
    private static final Pattern TRACEPARENT = Pattern.compile("^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$");
    private static final Pattern REQUEST_ID_FORMAT = Pattern.compile("^[A-Za-z0-9._:-]{1,128}$");
    private static final String ALL_ZERO_TRACE_ID = "0".repeat(32);
    private static final String ALL_ZERO_SPAN_ID = "0".repeat(16);

    private record State(String traceparent, String traceId, String spanId, String requestId) {
    }

    private static final ThreadLocal<State> CURRENT = new ThreadLocal<>();

    private TraceContext() {
    }

    /** Ends the context opened by {@link #open}: the values from before it (if any) are put back. */
    public interface Scope extends AutoCloseable {
        @Override
        void close();
    }

    /**
     * Makes the given trace the current one for this thread until the returned scope is closed.
     *
     * @param traceparent the incoming {@code traceparent} header, or null
     * @param requestId   the incoming {@code x-request-id} header, or null
     */
    public static Scope open(String traceparent, String requestId) {
        State previous = CURRENT.get();
        String validTraceparent = validTraceparent(traceparent);
        String traceId = null;
        String spanId = null;
        if (validTraceparent != null) {
            Matcher matcher = TRACEPARENT.matcher(validTraceparent);
            matcher.matches();
            traceId = matcher.group(2);
            spanId = matcher.group(3);
        }
        String validRequestId = requestId != null && REQUEST_ID_FORMAT.matcher(requestId).matches()
                ? requestId : UUID.randomUUID().toString();
        apply(new State(validTraceparent, traceId, spanId, validRequestId));
        return () -> apply(previous);
    }

    /** The {@code x-request-id} of the current thread, or null when no context is open. */
    public static String currentRequestId() {
        State state = CURRENT.get();
        return state == null ? null : state.requestId();
    }

    /**
     * The headers to add to a call to ANOTHER WebBlock service (gRPC metadata, HTTP header, RabbitMQ header), so the trace
     * continues. Never add them to a call to an outside host (Knock, Keycloak, LLM): secure-coding.md.
     * Empty when no context is open.
     */
    public static Map<String, String> outgoingHeaders() {
        State state = CURRENT.get();
        Map<String, String> headers = new LinkedHashMap<>();
        if (state != null) {
            if (state.traceparent() != null) {
                headers.put(TRACEPARENT_HEADER, state.traceparent());
            }
            headers.put(REQUEST_ID_HEADER, state.requestId());
        }
        return headers;
    }

    /** @return the header unchanged when it is a well-formed, non-zero {@code traceparent}; otherwise null */
    static String validTraceparent(String header) {
        if (header == null) {
            return null;
        }
        Matcher matcher = TRACEPARENT.matcher(header);
        if (!matcher.matches() || "ff".equals(matcher.group(1))
                || ALL_ZERO_TRACE_ID.equals(matcher.group(2)) || ALL_ZERO_SPAN_ID.equals(matcher.group(3))) {
            return null;
        }
        return header;
    }

    private static void apply(State state) {
        if (state == null) {
            CURRENT.remove();
            MDC.remove(TRACE_ID);
            MDC.remove(SPAN_ID);
            MDC.remove(REQUEST_ID);
            return;
        }
        CURRENT.set(state);
        put(TRACE_ID, state.traceId());
        put(SPAN_ID, state.spanId());
        put(REQUEST_ID, state.requestId());
    }

    private static void put(String key, String value) {
        if (value == null) {
            MDC.remove(key);
        } else {
            MDC.put(key, value);
        }
    }
}
