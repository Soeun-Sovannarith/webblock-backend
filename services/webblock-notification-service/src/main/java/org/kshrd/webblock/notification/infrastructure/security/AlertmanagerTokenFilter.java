package org.kshrd.webblock.notification.infrastructure.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Lets a request through only with "Authorization: Bearer &lt;webblock.notification.alertmanager.webhook-token&gt;".
 * Fails closed: with no token configured every call gets 401.
 * Not a Spring bean on purpose (Boot would then run it on every path); SecurityConfig adds it to the Alertmanager chain only.
 */
public class AlertmanagerTokenFilter extends OncePerRequestFilter {

    private final byte[] expected;

    public AlertmanagerTokenFilter(String token) {
        this.expected = token == null || token.isBlank() ? null : ("Bearer " + token).getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String header = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (expected == null || header == null
                || !MessageDigest.isEqual(expected, header.getBytes(StandardCharsets.UTF_8))) {
            // setStatus, not sendError: sendError would dispatch to /error and pass the main (JWT) chain
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            return;
        }
        chain.doFilter(request, response);
    }
}
