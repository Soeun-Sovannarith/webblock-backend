---
name: api-endpoint
description: Use when creating or changing a REST endpoint in a Spring Boot service. Contract first, then use case, controller, DTOs, validation, security and tests.
---

# Create or change a REST endpoint

## Purpose
Add or change one endpoint end to end while keeping the contract, layering, security and tests aligned.

## When to use / NOT to use
- Use: new endpoint, new field/status code on an existing one, new query parameter.
- Not for: error mapping only (`exception-handling`), schema changes (`flyway-migration`), calling another service (`feign-client`). If the change needs another service's code, stop and write a note for its owner.

## Inputs to confirm with the user
- Method, path (`/api/v1/...`), request and response fields, success status.
- Who may call it (roles) and who owns the resource (ownership rule).
- Is it a list (page/size/sort)? A retriable mutation (needs `Idempotency-Key`)?
- Is the operation already in `contracts/openapi/<service>.vN.yaml`?

## Procedure
1. **Contract first.** Add or verify the operation in the OpenAPI file (use the root `contract-change` skill). Additive only on a released version. Do not code an endpoint that is not in the contract.
2. **Domain.** Put rules in `domain/`; add repository methods to the domain interface and implement them in `infrastructure/`.
3. **Use case** in `application/`: one class, command/query in, application DTO out, `@Transactional`, ownership check.
4. **DTOs** in `presentation/` as records with Bean Validation.
5. **Controller**: validate, call the use case, map, return the right status (201 + `Location` on create).
6. **Security**: authenticated by default, `@PreAuthorize` for the role, ownership in the use case.
7. **Errors**: throw domain exceptions; mapping is in the advice (`exception-handling`). No try/catch in controllers.
8. **Tests**: use case unit tests; `@WebMvcTest` for 400/401/403/wrong owner; integration test if DB or messaging is involved.
9. **Verify**: `./gradlew test`, report the real result.

## Templates
```java
// presentation/dto/CreateProjectRequest.java
public record CreateProjectRequest(
    @NotBlank @Size(max = 100) String name,
    @Size(max = 500) String description) {}

// application/usecase/CreateProjectUseCase.java
@Service @RequiredArgsConstructor
public class CreateProjectUseCase {
    private final ProjectRepository projects;              // domain port
    @Transactional
    public ProjectView execute(CreateProjectCommand cmd) {
        Project p = Project.create(cmd.ownerId(), cmd.name(), cmd.description());
        return ProjectView.from(projects.save(p));
    }
}

// presentation/ProjectController.java
@RestController @RequestMapping("/api/v1/projects") @RequiredArgsConstructor
class ProjectController {
    private final CreateProjectUseCase createProject;
    @PostMapping
    @PreAuthorize("hasRole('USER')")
    ResponseEntity<ProjectResponse> create(@Valid @RequestBody CreateProjectRequest req,
                                           @AuthenticationPrincipal Jwt jwt) {
        var view = createProject.execute(new CreateProjectCommand(UUID.fromString(jwt.getSubject()), req.name(), req.description()));
        return ResponseEntity.created(URI.create("/api/v1/projects/" + view.id())).body(ProjectResponse.from(view));
    }
}
```
The owner id comes from the token (`jwt.getSubject()`), never from the body.

## Checklist
- [ ] Operation, fields and status codes match the contract exactly.
- [ ] No entity or domain object in the signature; no `Map<String,Object>`.
- [ ] Lists use `Pageable` with max size 100.
- [ ] 401/403/wrong-owner paths tested.
- [ ] Springdoc annotations match the contract.
- [ ] Nothing sensitive in logs.

## Common mistakes
- Coding first and fixing the contract afterwards.
- Taking `ownerId` or `userId` from the request.
- Putting business rules or `@Transactional` in the controller.
- Returning 200 for create, or 500 for validation and business errors.
- Forgetting `@Valid`, so constraints never run.

## Definition of done
Contract merged or in the same PR, code matches it, tests added, `./gradlew test` green, no unrelated changes. Hand off consumer notes if a field changed.

## Related
Skills: `contract-change` (root), `exception-handling`, `jpa-entity`, `flyway-migration`. Rules: `api-design`, `clean-architecture`, `spring-security`, `testing`.
