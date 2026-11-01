---
name: jpa-entity
description: Use when adding or changing a persistent entity in a Spring Boot service (JPA entity, Spring Data repository, mapping to the domain model).
---

# Add or change a JPA entity

## Purpose
Persist a domain object without leaking JPA into the domain: entity + Spring Data repository + mapper, behind a domain repository port.

## When to use / NOT to use
- Use: new aggregate, new column mapped, new query method.
- Not for: the SQL itself (`flyway-migration` first), exposing data via API (`api-endpoint`), reading another service's tables (forbidden; use Feign or events).

## Inputs to confirm with the user
- Fields, types, nullability, uniqueness, relations.
- Id strategy used in this service (UUID or identity); concurrent edits (`@Version`)?
- Which queries and list endpoints need indexes?

## Procedure
1. Write the Flyway migration first and have it merged or in the same PR.
2. Keep the domain model free of JPA. Create `XxxEntity` in `infrastructure/persistence/`.
3. Create the Spring Data interface in `infrastructure/persistence/`.
4. Create a mapper entity <-> domain (MapStruct or manual) in `infrastructure/persistence/`.
5. Implement the domain repository port in an adapter that wraps the Spring Data interface and mapper.
6. Test with `@DataJpaTest` + Testcontainers PostgreSQL (save/load, constraints, key queries).
7. Run `./gradlew test`.

## Templates
```java
// infrastructure/persistence/ProjectEntity.java
@Entity @Table(name = "project")
@Getter @Builder @NoArgsConstructor(access = AccessLevel.PROTECTED) @AllArgsConstructor
public class ProjectEntity {
    @Id private UUID id;
    @Column(name = "owner_id", nullable = false) private UUID ownerId;
    @Column(nullable = false, length = 100) private String name;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 20) private ProjectStatus status;
    @Version private long version;
    @Column(name = "created_at", nullable = false, updatable = false) private Instant createdAt;
    @Column(name = "updated_at", nullable = false) private Instant updatedAt;

    @Override public boolean equals(Object o) {
        return o instanceof ProjectEntity other && id != null && id.equals(other.id);
    }
    @Override public int hashCode() { return getClass().hashCode(); }
}

// infrastructure/persistence/ProjectJpaRepository.java
interface ProjectJpaRepository extends JpaRepository<ProjectEntity, UUID> {
    @Query("select p from ProjectEntity p where p.ownerId = :ownerId")
    Page<ProjectEntity> findByOwner(@Param("ownerId") UUID ownerId, Pageable pageable);
}

// infrastructure/persistence/ProjectRepositoryAdapter.java
@Repository @RequiredArgsConstructor
class ProjectRepositoryAdapter implements ProjectRepository {   // domain port
    private final ProjectJpaRepository jpa;
    private final ProjectMapper mapper;
    public Project save(Project p) { return mapper.toDomain(jpa.save(mapper.toEntity(p))); }
}
```

## Checklist
- [ ] Table, columns, types and nullability match the migration (`validate` passes).
- [ ] `LAZY` associations; enums as `STRING`; money `BigDecimal`; time `Instant`.
- [ ] No `@Data`, no all-field `equals/hashCode`.
- [ ] Lists use `Pageable`; N+1 avoided with `@EntityGraph`.
- [ ] Entity never returned by a controller or put in an event.

## Common mistakes
- Annotating the domain class with JPA to save a mapper.
- `FetchType.EAGER`, or bidirectional collections without need.
- `@Enumerated(ORDINAL)`.
- String-concatenated JPQL.
- Setting `ddl-auto=update` to "make it work".

## Definition of done
Migration + entity + adapter + tests, `./gradlew test` green, no layering violation.

## Related
Skills: `flyway-migration`, `api-endpoint`. Agents: `architecture-checker`, `migration-checker`. Rules: `jpa-postgres`, `clean-architecture`.
