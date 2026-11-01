package org.kshrd.webblock.project.common.presentation.filter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** Gives every request an ID before anything else runs, security included. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestIdFilter extends OncePerRequestFilter {

    public static String currentRequestId(HttpServletRequest request) {
        Object id = request.getAttribute(RequestIds.ATTRIBUTE);
        if (id instanceof String value) {
            return value;
        }
        String generated = Uuids.v7().toString();
        request.setAttribute(RequestIds.ATTRIBUTE, generated);
        return generated;
    }

    /** The container's ERROR dispatch must log with the request's own ID, so the filter runs again and reuses it. */
    @Override
    protected boolean shouldNotFilterErrorDispatch() {
        return false;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String id;
        if (request.getAttribute(RequestIds.ATTRIBUTE) instanceof String existing) {
            id = existing; // the same request again (ERROR dispatch): never a second ID
        } else {
            id = RequestIds.acceptedOrNew(request.getHeader(RequestIds.HEADER));
            request.setAttribute(RequestIds.ATTRIBUTE, id);
        }
        response.setHeader(RequestIds.HEADER, id);
        MDC.put(RequestIds.MDC_KEY, id);
        try {
            chain.doFilter(request, response);
        } finally {
            MDC.remove(RequestIds.MDC_KEY);
        }
    }
}
