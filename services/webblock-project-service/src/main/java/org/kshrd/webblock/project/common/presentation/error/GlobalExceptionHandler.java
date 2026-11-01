package org.kshrd.webblock.project.common.presentation.error;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCategory;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.MessageSourceResolvable;
import org.springframework.core.MethodParameter;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.dao.PessimisticLockingFailureException;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.AuthenticationException;
import org.springframework.validation.FieldError;
import org.springframework.validation.ObjectError;
import org.springframework.validation.method.ParameterErrors;
import org.springframework.validation.method.ParameterValidationResult;
import org.springframework.web.ErrorResponse;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import tools.jackson.core.exc.StreamConstraintsException;

/** Maps every exception that leaves a controller to a Problem Details body built by {@link ProblemDetailFactory}. */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    /** Database constraint name (as PostgreSQL reports it) to the code its violation reports; from every feature. */
    private final Map<String, ErrorCode> byDatabaseConstraint;

    private final long retryAfterSeconds;

    /**
     * Each feature contributes its constraint names with a {@link ConstraintErrorRegistration} bean; a constraint
     * mapped to two different codes stops the start-up.
     */
    public GlobalExceptionHandler(
            List<ConstraintErrorRegistration> constraintRegistrations,
            @Value("${webblock.http.retry-after-seconds:5}") long retryAfterSeconds) {
        Map<String, ErrorCode> merged = new HashMap<>();
        for (ConstraintErrorRegistration registration : constraintRegistrations) {
            registration.constraints().forEach((name, code) -> {
                ErrorCode previous = merged.putIfAbsent(name, code);
                if (previous != null && previous != code) {
                    throw new IllegalStateException("Constraint " + name + " is mapped to two error codes");
                }
            });
        }
        this.byDatabaseConstraint = Map.copyOf(merged);
        this.retryAfterSeconds = retryAfterSeconds;
    }

    @ExceptionHandler(WebBlockException.class)
    public ResponseEntity<ProblemDetail> handleWebBlock(WebBlockException ex, HttpServletRequest request) {
        if (ex.errorCode().category() == ErrorCategory.INTERNAL) {
            log.error("Request failed with {}", ex.errorCode().code(), ex);
        }
        return respond(request, ex.errorCode(), ex.details());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ProblemDetail> handleInvalidBody(
            MethodArgumentNotValidException ex, HttpServletRequest request) {
        List<ErrorDetail> details = new ArrayList<>();
        for (FieldError error : ex.getBindingResult().getFieldErrors()) {
            details.add(FieldErrors.of(error.getField(), error.getDefaultMessage(), error.getCode()));
        }
        for (ObjectError error : ex.getBindingResult().getGlobalErrors()) {
            details.add(FieldErrors.of(error.getObjectName(), error.getDefaultMessage(), error.getCode()));
        }
        return respond(request, ErrorCode.VALIDATION_FAILED, details);
    }

    @ExceptionHandler(HandlerMethodValidationException.class)
    public ResponseEntity<ProblemDetail> handleInvalidParameters(
            HandlerMethodValidationException ex, HttpServletRequest request) {
        List<ErrorDetail> details = new ArrayList<>();
        for (ParameterValidationResult result : ex.getParameterValidationResults()) {
            String name = parameterName(result.getMethodParameter());
            for (MessageSourceResolvable error : result.getResolvableErrors()) {
                if (result instanceof ParameterErrors && error instanceof FieldError field) {
                    // A @Valid body checked by method validation: name the field, as handleInvalidBody does.
                    details.add(FieldErrors.of(field.getField(), field.getDefaultMessage(), field.getCode()));
                } else {
                    details.add(FieldErrors.of(name, error.getDefaultMessage(), constraintName(error.getCodes())));
                }
            }
        }
        return respond(request, ErrorCode.VALIDATION_FAILED, details);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ProblemDetail> handleConstraintViolation(
            ConstraintViolationException ex, HttpServletRequest request) {
        List<ErrorDetail> details = new ArrayList<>();
        for (ConstraintViolation<?> violation : ex.getConstraintViolations()) {
            details.add(FieldErrors.of(violation));
        }
        return respond(request, ErrorCode.VALIDATION_FAILED, details);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ProblemDetail> handleUnreadable(
            HttpMessageNotReadableException ex, HttpServletRequest request) {
        Throwable cause = ex;
        for (int depth = 0; cause != null && depth < 10; depth++, cause = cause.getCause()) {
            if (cause instanceof BodyTooLargeException) {
                return respond(request, ErrorCode.PAYLOAD_TOO_LARGE, List.of());
            }
        }
        return respond(request, ErrorCode.MALFORMED_REQUEST, List.of());
    }

    /** Jackson's size and depth limits, hit outside a request body read (that case arrives wrapped, above). */
    @ExceptionHandler(StreamConstraintsException.class)
    public ResponseEntity<ProblemDetail> handleStreamConstraints(HttpServletRequest request) {
        return respond(request, ErrorCode.MALFORMED_REQUEST, List.of());
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ProblemDetail> handleMissingHeader(
            MissingRequestHeaderException ex, HttpServletRequest request) {
        return respond(
                request,
                ErrorCode.VALIDATION_FAILED,
                List.of(ErrorDetail.of(ex.getHeaderName(), ErrorCode.FIELD_REQUIRED)));
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<ProblemDetail> handleMissingParameter(
            MissingServletRequestParameterException ex, HttpServletRequest request) {
        return respond(
                request,
                ErrorCode.VALIDATION_FAILED,
                List.of(ErrorDetail.of(ex.getParameterName(), ErrorCode.FIELD_REQUIRED)));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ProblemDetail> handleTypeMismatch(
            MethodArgumentTypeMismatchException ex, HttpServletRequest request) {
        ErrorCode code = UUID.class.equals(ex.getRequiredType()) ? ErrorCode.UUID_INVALID : ErrorCode.VALUE_INVALID;
        return respond(request, ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of(ex.getName(), code)));
    }

    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<ProblemDetail> handleNoRoute(HttpServletRequest request) {
        return respond(request, ErrorCode.ROUTE_NOT_FOUND, List.of());
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ProblemDetail> handleMethod(
            HttpRequestMethodNotSupportedException ex, HttpServletRequest request) {
        ErrorCode code = ErrorCode.METHOD_NOT_ALLOWED;
        HttpMethod[] allowed = ex.getSupportedHttpMethods() == null
                ? new HttpMethod[0]
                : ex.getSupportedHttpMethods().toArray(HttpMethod[]::new);
        return start(request, code).allow(allowed).body(ProblemDetailFactory.of(code, List.of(), request));
    }

    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    public ResponseEntity<ProblemDetail> handleMediaType(HttpServletRequest request) {
        return respond(request, ErrorCode.UNSUPPORTED_MEDIA_TYPE, List.of());
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<ProblemDetail> handleTooLarge(HttpServletRequest request) {
        return respond(request, ErrorCode.PAYLOAD_TOO_LARGE, List.of());
    }

    @ExceptionHandler(OptimisticLockingFailureException.class)
    public ResponseEntity<ProblemDetail> handleOptimisticLock(HttpServletRequest request) {
        return respond(request, ErrorCode.CONCURRENT_UPDATE, List.of());
    }

    /**
     * A lock wait or statement that ran into the project database's timeouts, or no free pooled connection (reached
     * through {@link #handleUnexpected}): the database is busy, try again ({@link DatabaseBusy}).
     */
    @ExceptionHandler({PessimisticLockingFailureException.class, QueryTimeoutException.class})
    public ResponseEntity<ProblemDetail> handleDatabaseBusy(Exception ex, HttpServletRequest request) {
        log.warn("Database busy: {}", ex.getClass().getSimpleName());
        return respond(request, ErrorCode.DEPENDENCY_UNAVAILABLE, List.of());
    }

    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ProblemDetail> handleIntegrity(
            DataIntegrityViolationException ex, HttpServletRequest request) {
        Optional<String> constraint = databaseConstraintName(ex);
        ErrorCode code = constraint.map(byDatabaseConstraint::get).orElse(null);
        if (code == null) {
            // The constraint name only: PostgreSQL's message can quote row values, which may be customer content.
            log.error("Unmapped data integrity violation, constraint={}", constraint.orElse("unknown"));
            code = ErrorCode.INTERNAL_ERROR;
        }
        return respond(request, code, List.of());
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ProblemDetail> handleAccessDenied(HttpServletRequest request) {
        return respond(request, ErrorCode.ACCESS_DENIED, List.of());
    }

    @ExceptionHandler(AuthenticationException.class)
    public ResponseEntity<ProblemDetail> handleAuthentication(HttpServletRequest request) {
        return respond(request, ErrorCode.UNAUTHENTICATED, List.of());
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ProblemDetail> handleUnexpected(Exception ex, HttpServletRequest request) {
        if (ex instanceof ErrorResponse errorResponse
                && errorResponse.getStatusCode().is4xxClientError()) {
            return respond(
                    request,
                    StatusErrorCodes.forStatus(errorResponse.getStatusCode().value()),
                    List.of());
        }
        if (DatabaseBusy.is(ex)) {
            return handleDatabaseBusy(ex, request);
        }
        log.error("Unhandled exception", ex);
        return respond(request, ErrorCode.INTERNAL_ERROR, List.of());
    }

    private ResponseEntity<ProblemDetail> respond(
            HttpServletRequest request, ErrorCode code, List<ErrorDetail> details) {
        return start(request, code).body(ProblemDetailFactory.of(code, details, request));
    }

    /**
     * The code's status, the content type chosen from {@code Accept} (preset, so Spring MVC does not negotiate it
     * again), {@code Retry-After} where spec 6.3 keeps it, and the Bearer challenge on a 401.
     */
    private ResponseEntity.BodyBuilder start(HttpServletRequest request, ErrorCode code) {
        ResponseEntity.BodyBuilder builder = ResponseEntity.status(
                        code.category().httpStatus())
                .contentType(ProblemDetailFactory.contentType(request));
        // A dependency error, or a pending admission release (the same request succeeds once the release completed).
        if (code.category() == ErrorCategory.DEPENDENCY || code == ErrorCode.ADMISSION_RELEASE_PENDING) {
            builder.header(HttpHeaders.RETRY_AFTER, Long.toString(retryAfterSeconds));
        }
        if (code == ErrorCode.UNAUTHENTICATED) {
            builder.header(HttpHeaders.WWW_AUTHENTICATE, "Bearer");
        }
        return builder;
    }

    /** Spring lists a constraint's message codes from most to least specific; the last is the bare constraint name. */
    private static String constraintName(String[] codes) {
        return codes == null || codes.length == 0 ? null : codes[codes.length - 1];
    }

    /** The violated database constraint, named by PostgreSQL, anywhere in the cause chain. */
    private static Optional<String> databaseConstraintName(Throwable failure) {
        for (Throwable t = failure; t != null; t = t.getCause()) {
            if (t instanceof org.hibernate.exception.ConstraintViolationException violation
                    && violation.getConstraintName() != null) {
                return Optional.of(violation.getConstraintName());
            }
        }
        return Optional.empty();
    }

    private static String parameterName(MethodParameter parameter) {
        RequestHeader header = parameter.getParameterAnnotation(RequestHeader.class);
        if (header != null) {
            return !header.name().isEmpty() ? header.name() : header.value();
        }
        String name = parameter.getParameterName();
        return name == null ? "parameter" : name;
    }
}
