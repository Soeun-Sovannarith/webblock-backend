---
paths:
  - "**/*.java"
---

# Spring Security rules (Keycloak resource server)

## Authentication
- Resource server via `spring-boot-starter-oauth2-resource-server`: validate issuer, audience, signature and expiry. Never parse or trust a token by hand.
- Default deny: `anyRequest().authenticated()`. Public endpoints are an explicit, documented allow-list (actuator `health`/`info`, public auth endpoints).
- Stateless sessions. CSRF is disabled only because the API is token-based and cookie-free. CORS lists known frontend origins, never `*` with credentials.

## Authorization (role AND ownership)
- Roles via `@PreAuthorize("hasRole('...')")`; ownership checked in the use case (`resource.ownerId == jwt.sub`).
- Never trust a user id from the body or query string.
- Map Keycloak roles (`realm_access.roles` / `resource_access`) in one `JwtAuthenticationConverter` bean; no scattered role parsing.

## Sensitive data and exposure
- Passwords, OTPs, tokens: never log, return or store in plaintext; hashing and OTP are delegated to Keycloak or a vetted library.
- Actuator: only `health` and `info` public; no `exposure.include=*` outside local profiles.
- Keep default security headers unless the gateway sets them.

## Tests
- Every endpoint has tests for 401 (no token), 403 (wrong role) and 403/404 (wrong owner).

## Example
```java
http.authorizeHttpRequests(a -> a.requestMatchers("/actuator/health", "/actuator/info").permitAll()
        .anyRequest().authenticated())
    .oauth2ResourceServer(o -> o.jwt(j -> j.jwtAuthenticationConverter(keycloakConverter)));
```
