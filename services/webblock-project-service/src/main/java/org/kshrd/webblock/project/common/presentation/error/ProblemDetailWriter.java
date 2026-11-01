package org.kshrd.webblock.project.common.presentation.error;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ProblemDetail;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

/**
 * Writes a {@link ProblemDetailFactory} body straight to the servlet response, for errors raised outside Spring MVC's
 * return-value handling: the request-body limit, the security chain's 401 and 403, and the container's error dispatch.
 * The mapper is Spring Boot's, whose {@code ProblemDetailJacksonMixin} renders the properties as top-level members.
 */
@Component
public class ProblemDetailWriter {

    private final ObjectMapper objectMapper;

    public ProblemDetailWriter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /**
     * Sets the code's status and the negotiated content type, adds {@code WWW-Authenticate: Bearer} to a 401 unless
     * the caller already set a challenge, drops any uncommitted buffered output, and writes the body. The caller adds
     * any other header first.
     */
    public void write(HttpServletRequest request, HttpServletResponse response, ErrorCode code) throws IOException {
        ProblemDetail problem = ProblemDetailFactory.of(code, List.of(), request);
        if (code == ErrorCode.UNAUTHENTICATED && !response.containsHeader(HttpHeaders.WWW_AUTHENTICATE)) {
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Bearer");
        }
        if (!response.isCommitted()) {
            // Nothing a half-run handler buffered may precede the JSON; headers already set are kept.
            response.resetBuffer();
        }
        response.setStatus(problem.getStatus());
        response.setContentType(ProblemDetailFactory.contentType(request).toString());
        objectMapper.writeValue(response.getOutputStream(), problem);
    }
}
