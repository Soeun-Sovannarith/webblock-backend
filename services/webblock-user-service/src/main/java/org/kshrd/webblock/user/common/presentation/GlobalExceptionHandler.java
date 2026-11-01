package org.kshrd.webblock.user.common.presentation;

import jakarta.servlet.http.HttpServletRequest;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.net.URI;
import java.util.List;
import java.util.Map;

// Turns errors into RFC 9457 problem+json with a stable "code".
// Never sends stack traces or the values the caller sent.
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    // Our own known errors (thrown by handlers)
    @ExceptionHandler(ApiException.class)
    ResponseEntity<ProblemDetail> handleApi(ApiException ex, HttpServletRequest request) {
        return problem(ex.getErrorCode(), request, List.of());
    }

    // @Valid failed on a request body
    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ProblemDetail> handleValidation(MethodArgumentNotValidException ex, HttpServletRequest request) {
        List<Map<String, String>> errors = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> Map.of(
                        "field", e.getField(),
                        "code", e.getCode() == null ? "INVALID" : e.getCode().toUpperCase(),
                        "message", e.getDefaultMessage() == null ? "Not valid." : e.getDefaultMessage()))
                .toList();
        return problem(ErrorCode.VALIDATION_FAILED, request, errors);
    }

    // Body is missing or is not valid JSON
    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<ProblemDetail> handleUnreadable(HttpMessageNotReadableException ex, HttpServletRequest request) {
        return problem(ErrorCode.VALIDATION_FAILED, request, List.of());
    }

    // @PreAuthorize said no (for example a normal user on an admin method)
    @ExceptionHandler(AccessDeniedException.class)
    ResponseEntity<ProblemDetail> handleAccessDenied(AccessDeniedException ex, HttpServletRequest request) {
        return problem(ErrorCode.ACCESS_DENIED, request, List.of());
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ProblemDetail> handleOther(Exception ex, HttpServletRequest request) {
        // Spring's own web errors (404 unknown path, 405 wrong method, 415 wrong content type...) keep their status
        if (ex instanceof ErrorResponse springError) {
            int status = springError.getStatusCode().value();
            ProblemDetail body = baseProblem(status, request);
            body.setTitle(springError.getBody().getTitle());
            body.setProperty("code", "HTTP_" + status);
            body.setProperty("errors", List.of());
            return ResponseEntity.status(status).body(body);
        }
        // A real bug: full details go to the log only, never to the caller
        log.error("Unexpected error on {} {}", request.getMethod(), request.getRequestURI(), ex);
        return problem(ErrorCode.INTERNAL_ERROR, request, List.of());
    }

    private ResponseEntity<ProblemDetail> problem(ErrorCode code, HttpServletRequest request, List<?> errors) {
        ProblemDetail body = baseProblem(code.status(), request);
        body.setType(URI.create("urn:webblock:problem:" + code.name().toLowerCase().replace('_', '-')));
        body.setTitle(code.title());
        body.setProperty("code", code.name());
        body.setProperty("errors", errors);
        return ResponseEntity.status(code.status()).body(body);
    }

    private ProblemDetail baseProblem(int status, HttpServletRequest request) {
        ProblemDetail body = ProblemDetail.forStatus(status);
        body.setInstance(URI.create(request.getRequestURI()));
        return body;
    }
}
