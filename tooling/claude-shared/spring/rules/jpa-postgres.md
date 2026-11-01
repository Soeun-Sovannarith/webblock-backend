---
paths:
  - "**/*.java"
---

# JPA and PostgreSQL rules

## Schema ownership
- Flyway owns the schema. `spring.jpa.hibernate.ddl-auto` is `validate` (or `none`); never `update` or `create`.
- One database per service. Never query another service's tables; use Feign or events.

## Mapping
- Primary keys: UUID or `bigint identity`; stay consistent within the service. Do not leak internal sequential ids across services unless the contract says so.
- Associations are `LAZY`; never `FetchType.EAGER`. Avoid bidirectional `@OneToMany` unless needed.
- Enums: `@Enumerated(EnumType.STRING)`. Money: `BigDecimal` / `numeric(19,4)`, never `double`.
- Time: `Instant` or `OffsetDateTime` as `timestamptz` (UTC). Tables carry `created_at` / `updated_at`.
- `@Version` on aggregates edited concurrently.
- `equals/hashCode` on id or business key, never all fields; no `@Data` on entities.

## Queries
- Derived queries or `@Query` with named parameters; never string concatenation.
- Any list uses `Pageable`; no unbounded result sets.
- Prevent N+1 with `@EntityGraph` or fetch joins and assert it in a test.
- Index foreign keys and frequent WHERE/ORDER BY columns, in a Flyway migration.
- Soft delete only if the service `CLAUDE.md` says so.

## Example
```java
@Query("select p from ProjectEntity p where p.ownerId = :ownerId")
Page<ProjectEntity> findByOwner(@Param("ownerId") UUID ownerId, Pageable pageable);
```
