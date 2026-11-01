package org.kshrd.webblock.project.common.presentation.error;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.sql.SQLException;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.common.application.validation.IdempotencyKey;
import org.kshrd.webblock.project.common.application.validation.PagePath;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.presentation.envelope.PagedResult;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.InsufficientAuthenticationException;
import org.springframework.web.ErrorResponseException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/** Endpoints that exist only in tests, to drive the envelope, the handler and security. */
@RestController
public class ProbeController {

    public record ProbeRequest(
            @NotBlank @Size(max = 10) String name, @PagePath String path) {}

    public record ProbeView(String name) {}

    public record ScalarRequest(int count, boolean flag, double ratio) {}

    @PostMapping("/api/test-probe")
    public ResponseEntity<ProbeView> create(@Valid @RequestBody ProbeRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED).body(new ProbeView(request.name()));
    }

    @GetMapping("/api/test-probe")
    public ProbeView get() {
        return new ProbeView("probe");
    }

    @GetMapping("/api/test-probe/list")
    public PagedResult<ProbeView> list() {
        return new PagedResult<>(List.of(new ProbeView("a")), 20, "c1");
    }

    @GetMapping("/internal/test-probe")
    public ProbeView internal() {
        return new ProbeView("internal");
    }

    @GetMapping("/api/test-probe/error-entity")
    public ResponseEntity<ProbeView> errorEntity() {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(new ProbeView("conflict"));
    }

    @GetMapping("/api/test-probe/problem")
    public ProblemDetail problem() {
        return ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "problem");
    }

    @GetMapping(value = "/api/test-probe/string", produces = MediaType.TEXT_PLAIN_VALUE)
    public String string() {
        return "plain";
    }

    @GetMapping(value = "/api/test-probe/bytes", produces = MediaType.APPLICATION_OCTET_STREAM_VALUE)
    public byte[] bytes() {
        return new byte[] {1, 2, 3};
    }

    @GetMapping(value = "/api/test-probe/resource", produces = MediaType.APPLICATION_OCTET_STREAM_VALUE)
    public Resource resource() {
        return new ByteArrayResource(new byte[] {4, 5});
    }

    @PostMapping("/api/test-probe/keyed")
    public ProbeView keyed(@RequestHeader("Idempotency-Key") @IdempotencyKey String key) {
        return new ProbeView("keyed");
    }

    /** A constrained header and a @Valid body: Spring MVC validates both through method validation. */
    @PostMapping("/api/test-probe/keyed-body")
    public ProbeView keyedBody(
            @RequestHeader("Idempotency-Key") @IdempotencyKey String key, @Valid @RequestBody ProbeRequest request) {
        return new ProbeView(request.name());
    }

    @GetMapping("/api/test-probe/items/{id}")
    public ProbeView item(@PathVariable UUID id) {
        return new ProbeView(id.toString());
    }

    @GetMapping("/api/test-probe/conflict")
    public ProbeView conflict() {
        throw new WebBlockException(ErrorCode.CONCURRENT_UPDATE);
    }

    @GetMapping("/api/test-probe/boom")
    public ProbeView boom() {
        throw new IllegalStateException("secret-internal-detail");
    }

    @GetMapping("/api/test-probe/duplicate")
    public ProbeView duplicate() {
        throw new DataIntegrityViolationException(
                "duplicate",
                new org.hibernate.exception.ConstraintViolationException(
                        "duplicate", new SQLException("duplicate key value"), "probe_name_key"));
    }

    @GetMapping("/api/test-probe/unmapped")
    public ProbeView unmapped() {
        throw new DataIntegrityViolationException(
                "duplicate",
                new org.hibernate.exception.ConstraintViolationException(
                        "duplicate", new SQLException("duplicate key value"), "probe_unmapped_key"));
    }

    @GetMapping("/api/test-probe/stale")
    public ProbeView stale() {
        throw new ObjectOptimisticLockingFailureException(ProbeView.class, "id");
    }

    @PostMapping("/api/test-probe/scalars")
    public ProbeView scalars(@RequestBody ScalarRequest request) {
        return new ProbeView("scalars");
    }

    @GetMapping("/api/test-probe/unavailable")
    public ProbeView unavailable() {
        throw new ErrorResponseException(HttpStatus.SERVICE_UNAVAILABLE);
    }

    @GetMapping("/api/test-probe/dependency")
    public ProbeView dependency() {
        throw new WebBlockException(ErrorCode.DEPENDENCY_UNAVAILABLE);
    }

    @GetMapping("/api/test-probe/throttled")
    public ProbeView throttled() {
        throw new ErrorResponseException(HttpStatus.TOO_MANY_REQUESTS);
    }

    /** An AccessDeniedException raised under Spring MVC, not in the filter chain. */
    @GetMapping("/api/test-probe/denied")
    public ProbeView denied() {
        throw new AccessDeniedException("secret-denial-detail");
    }

    /** An AuthenticationException raised under Spring MVC, not in the filter chain. */
    @GetMapping("/api/test-probe/unauthenticated")
    public ProbeView unauthenticated() {
        throw new InsufficientAuthenticationException("secret-auth-detail");
    }

    /** A framework exception whose own ProblemDetail carries a detail the response must never show. */
    @GetMapping("/api/test-probe/detailed")
    public ProbeView detailed() {
        throw new ErrorResponseException(
                HttpStatus.TOO_MANY_REQUESTS,
                ProblemDetail.forStatusAndDetail(HttpStatus.TOO_MANY_REQUESTS, "secret-framework-detail"),
                null);
    }
}
