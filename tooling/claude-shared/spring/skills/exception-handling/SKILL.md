---
name: exception-handling
description: Use when adding custom exceptions or changing error responses in a Spring Boot service (RFC 7807 problem details through a global handler).
---

# Exceptions and error responses

## Purpose
Turn typed domain errors into one consistent RFC 7807 `application/problem+json` format without leaking internals.

## When to use / NOT to use
- Use: new business error, new status mapping, changing the error body.
- Not for: choosing validation rules (`api-endpoint`) or security filter failures beyond mapping 401/403.

## Inputs to confirm with the user
- Error name, HTTP status, stable `type` URI/code, safe message.
- Is the error already listed in the OpenAPI `Problem` responses?

## Procedure
1. Add a typed exception in `domain/exception/` (or `application`). It carries a code, not an HTTP status.
2. Add the response to the OpenAPI contract if new (stable `type` and code are part of the contract).
3. Map it in the single `@RestControllerAdvice` in `presentation/`.
4. Make sure use cases throw it; remove try/catch from controllers.
5. Log the full exception server-side with the correlation id and no sensitive values.
6. One `@WebMvcTest` per mapping. Run `./gradlew test`.

## Templates
```java
// domain/exception/ProjectNotFoundException.java
public class ProjectNotFoundException extends DomainException {
    public ProjectNotFoundException(UUID id) { super("PROJECT_NOT_FOUND", "Project " + id + " not found"); }
}

// presentation/GlobalExceptionHandler.java
@RestControllerAdvice
@Slf4j
class GlobalExceptionHandler {
    private static final String BASE = "https://api.webblock.example/problems/";

    @ExceptionHandler(ProjectNotFoundException.class)
    ProblemDetail notFound(ProjectNotFoundException e) {
        return problem(HttpStatus.NOT_FOUND, "PROJECT_NOT_FOUND", "Project not found", e.getMessage());
    }
    @ExceptionHandler(MethodArgumentNotValidException.class)
    ProblemDetail invalid(MethodArgumentNotValidException e) {
        var p = problem(HttpStatus.BAD_REQUEST, "VALIDATION_FAILED", "Validation failed", "Request is invalid");
        p.setProperty("errors", e.getBindingResult().getFieldErrors().stream()
            .map(f -> Map.of("field", f.getField(), "message", String.valueOf(f.getDefaultMessage()))).toList());
        return p;
    }
    @ExceptionHandler(AccessDeniedException.class)
    ProblemDetail forbidden(AccessDeniedException e) {
        return problem(HttpStatus.FORBIDDEN, "FORBIDDEN", "Forbidden", "Access denied");
    }
    @ExceptionHandler(Exception.class)
    ProblemDetail unexpected(Exception e) {
        log.error("Unhandled error correlationId={}", MDC.get("correlationId"), e);
        return problem(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Internal error", "Unexpected error");
    }
    private ProblemDetail problem(HttpStatus s, String code, String title, String detail) {
        var p = ProblemDetail.forStatusAndDetail(s, detail);
        p.setType(URI.create(BASE + code.toLowerCase())); p.setTitle(title);
        p.setProperty("code", code); p.setProperty("correlationId", MDC.get("correlationId"));
        return p;
    }
}
```
Mapping: validation 400 (`errors[]`), not found 404, conflict 409, business rule 422, 401/403, open circuit or downstream failure 503, anything else 500. Spring Boot returns `application/problem+json` for `ProblemDetail` when problem details are enabled (`spring.mvc.problemdetails.enabled=true` if you also want framework exceptions covered). Replace the base URI with the one in the contract.

## Checklist
- [ ] Body has `type, title, status, detail, instance` (instance defaults to the request path) plus `code`, `correlationId`.
- [ ] No stack trace, SQL, class name, token or PII in any response.
- [ ] Codes and `type` URIs stable and in the contract.
- [ ] Wrong-owner returns 403 or 404 consistently with the service rule.

## Common mistakes
- Exceptions that carry HTTP status (domain depends on web).
- Echoing `e.getMessage()` of unknown exceptions to the client.
- Different error shapes in different controllers.
- Logging passwords, OTPs, tokens or emails inside messages.
- Revealing whether an email exists in auth flows.

## Definition of done
Mapping + contract entry + `@WebMvcTest` asserting status, body shape and no leaks; `./gradlew test` green.

## Related
Skills: `api-endpoint`, `feign-client`. Rules: `api-design`, `spring-security`.
