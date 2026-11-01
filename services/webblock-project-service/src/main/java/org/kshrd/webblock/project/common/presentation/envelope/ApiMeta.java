package org.kshrd.webblock.project.common.presentation.envelope;

import jakarta.servlet.http.HttpServletRequest;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.kshrd.webblock.project.common.presentation.filter.RequestIdFilter;
import org.slf4j.MDC;

public record ApiMeta(String requestId, String traceId, Instant timestamp, String path, int status, PageMeta page) {

    public static ApiMeta of(HttpServletRequest request, int status, PageMeta page) {
        return new ApiMeta(
                RequestIdFilter.currentRequestId(request),
                MDC.get(RequestIds.TRACE_ID_MDC_KEY),
                Instant.now().truncatedTo(ChronoUnit.MILLIS),
                request.getRequestURI(),
                status,
                page);
    }
}
