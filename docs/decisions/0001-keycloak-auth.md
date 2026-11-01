# 0001. Keycloak for authentication
- Status: Accepted
- Date: TODO(Vannda)
- Deciders: backend team

## Context
The platform needs sign-up, login (manual and Google), OTP, forgot/reset password and role-based access for several services.

## Decision
Use Keycloak as the identity provider (OIDC/JWT). Services act as OAuth2 resource servers and validate tokens themselves.

## Options considered
- Keycloak: standard protocols, social login, password policies, session management out of the box; one more component to operate.
- Custom auth in user-service: full control; high security risk and maintenance cost.

## Consequences
- No custom password hashing or token code in our services.
- Every service needs resource-server configuration and a shared role convention (`docs/auth-flow.md`).
- Realm and client configuration must be versioned (exported without secrets) and managed per environment.
