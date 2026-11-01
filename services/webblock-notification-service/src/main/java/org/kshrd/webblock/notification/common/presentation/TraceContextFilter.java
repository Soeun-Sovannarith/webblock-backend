package org.kshrd.webblock.notification.common.presentation;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Continues the incoming {@code traceparent} / {@code x-request-id} for every HTTP request (token endpoint, Alertmanager
 * webhook, actuator), runs first so that even the security filters log with the request id, and answers with the request id
 * so a caller can quote it in a bug report.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceContextFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        try (TraceContext.Scope ignored = TraceContext.open(
                request.getHeader(TraceContext.TRACEPARENT_HEADER), request.getHeader(TraceContext.REQUEST_ID_HEADER))) {
            response.setHeader(TraceContext.REQUEST_ID_HEADER, TraceContext.currentRequestId());
            chain.doFilter(request, response);
        }
    }
}
