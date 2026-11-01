package org.kshrd.webblock.project.common.presentation.error;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.springframework.http.HttpHeaders;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.oauth2.core.OAuth2AuthenticationException;
import org.springframework.security.oauth2.server.resource.BearerTokenErrorCodes;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.stereotype.Component;

/**
 * Writes the Problem Details body for 401 and 403, which the filter chain raises before any controller advice. A token
 * that was presented and refused gets {@code WWW-Authenticate: Bearer error="invalid_token"} (RFC 6750 section 3.1),
 * without Spring's error description, which can quote the token parser's message; any other 401 gets {@code Bearer}
 * from the writer.
 */
@Component
public class SecurityErrorHandlers implements AuthenticationEntryPoint, AccessDeniedHandler {

    private static final String INVALID_TOKEN_CHALLENGE =
            "Bearer error=\"" + BearerTokenErrorCodes.INVALID_TOKEN + "\"";

    private final ProblemDetailWriter problemDetailWriter;

    public SecurityErrorHandlers(ProblemDetailWriter problemDetailWriter) {
        this.problemDetailWriter = problemDetailWriter;
    }

    @Override
    public void commence(
            HttpServletRequest request, HttpServletResponse response, AuthenticationException authException)
            throws IOException {
        if (authException instanceof OAuth2AuthenticationException refused
                && BearerTokenErrorCodes.INVALID_TOKEN.equals(refused.getError().getErrorCode())) {
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, INVALID_TOKEN_CHALLENGE);
        }
        problemDetailWriter.write(request, response, ErrorCode.UNAUTHENTICATED);
    }

    @Override
    public void handle(
            HttpServletRequest request, HttpServletResponse response, AccessDeniedException accessDeniedException)
            throws IOException {
        problemDetailWriter.write(request, response, ErrorCode.ACCESS_DENIED);
    }
}
