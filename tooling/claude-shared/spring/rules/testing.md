---
paths:
  - "**/src/test/**"
---

# Testing rules (Spring Boot)

## Style
- JUnit 5, Mockito, AssertJ. Name `methodName_condition_expectedResult`. One behavior per test, Arrange / Act / Assert.
- Test data is obviously fake (`user@example.test`); never real data or credentials.

## Pyramid
- **Unit** (domain, application): plain JUnit, no Spring context, mock ports. The majority.
- **Slice** (`@WebMvcTest`, `@DataJpaTest`): validation, serialization, security, repository queries.
- **Integration** (`@SpringBootTest` + Testcontainers PostgreSQL/RabbitMQ): migrations, transactions, messaging. Few and focused.
- Never H2 in place of PostgreSQL.

## Must cover
Happy path, validation failure (400), 401, 403 and wrong owner, not found, conflict/duplicate, downstream failure (Feign timeout -> fallback), duplicate message (idempotency).

## Isolation
- Mock other services with WireMock or `@MockitoBean`; never call real Keycloak, payment providers or services.
- No `Thread.sleep`; use Awaitility. Tests do not depend on order or on each other's data.

## Discipline
- Never weaken or delete a failing test to get green; fix the cause or report it.
- A bug fix includes a regression test that fails without the fix.
- Report the real result of `./gradlew test`.

## Example
```java
@Test
void createProject_blankName_returns400() throws Exception {
    mvc.perform(post("/api/v1/projects").with(jwt()).contentType(APPLICATION_JSON)
            .content("{\"name\":\"\"}"))
       .andExpect(status().isBadRequest());
}
```
