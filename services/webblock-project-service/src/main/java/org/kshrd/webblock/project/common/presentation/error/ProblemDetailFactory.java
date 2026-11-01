package org.kshrd.webblock.project.common.presentation.error;

import jakarta.servlet.RequestDispatcher;
import jakarta.servlet.http.HttpServletRequest;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Enumeration;
import java.util.List;
import java.util.Locale;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.kshrd.webblock.project.common.presentation.filter.RequestIdFilter;
import org.slf4j.MDC;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.web.util.UriUtils;

/**
 * Builds every error body of the service as an RFC 9457 Problem Details object (spec sections 6.3 and 6.4). The
 * {@code type} URN, {@code title} and {@code status} come from the {@link ErrorCode}; {@code instance} is the request
 * path; the extensions are {@code code}, {@code requestId}, {@code traceId} (only while tracing is active) and
 * {@code errors}. There is never a {@code detail}, so a body never echoes caller input.
 */
public final class ProblemDetailFactory {

    private static final String TYPE_PREFIX = "urn:webblock:problem:";

    private ProblemDetailFactory() {}

    public static ProblemDetail of(ErrorCode code, List<ErrorDetail> errors, HttpServletRequest request) {
        ProblemDetail problem = ProblemDetail.forStatus(code.category().httpStatus());
        problem.setType(type(code));
        problem.setTitle(code.message());
        problem.setInstance(instance(request));
        problem.setProperty("code", code.code());
        problem.setProperty("requestId", RequestIdFilter.currentRequestId(request));
        String traceId = MDC.get(RequestIds.TRACE_ID_MDC_KEY);
        if (traceId != null) {
            // Only when present: Spring's mixin writes a null property as "traceId": null.
            problem.setProperty("traceId", traceId);
        }
        problem.setProperty("errors", List.copyOf(errors));
        return problem;
    }

    /**
     * {@code application/problem+json} unless the client's {@code Accept} refuses it and accepts {@code
     * application/json}. An absent, unsatisfiable or unreadable {@code Accept} (a malformed
     * media type or quality value) gets {@code application/problem+json}.
     */
    public static MediaType contentType(HttpServletRequest request) {
        Enumeration<String> values = request.getHeaders(HttpHeaders.ACCEPT);
        try {
            List<MediaType> accepted = MediaType.parseMediaTypes(values == null ? List.of() : Collections.list(values));
            if (accepted.isEmpty() || accepts(accepted, MediaType.APPLICATION_PROBLEM_JSON)) {
                return MediaType.APPLICATION_PROBLEM_JSON;
            }
            return accepts(accepted, MediaType.APPLICATION_JSON)
                    ? MediaType.APPLICATION_JSON
                    : MediaType.APPLICATION_PROBLEM_JSON;
        } catch (IllegalArgumentException unreadable) {
            // Parsing, or reading a quality value (a NumberFormatException for "Q=abc"): an error is never turned
            // into a different failure.
            return MediaType.APPLICATION_PROBLEM_JSON;
        }
    }

    /** {@code urn:webblock:problem:} and the code in lower case with {@code _} replaced by {@code -}. */
    static URI type(ErrorCode code) {
        return URI.create(TYPE_PREFIX + code.code().toLowerCase(Locale.ROOT).replace('_', '-'));
    }

    /** The request path without the query; on the container's ERROR dispatch, the original request's path. */
    static URI instance(HttpServletRequest request) {
        String path = request.getAttribute(RequestDispatcher.ERROR_REQUEST_URI) instanceof String original
                ? original
                : request.getRequestURI();
        try {
            return URI.create(path);
        } catch (IllegalArgumentException notAUri) {
            // A container can hand over characters a URI forbids (a space, '|'): encode them, never fail the error.
            return URI.create(UriUtils.encodePath(path, StandardCharsets.UTF_8));
        }
    }

    private static boolean accepts(List<MediaType> accepted, MediaType type) {
        return accepted.stream().anyMatch(range -> range.getQualityValue() > 0 && range.includes(type));
    }
}
