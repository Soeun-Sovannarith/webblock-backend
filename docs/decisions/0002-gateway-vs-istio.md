# 0002. API gateway: Spring Cloud Gateway vs Istio
- Status: Proposed
- Date: TODO
- Deciders: backend team, DevOps

## Context
The backend has an `api-gateway` service (Spring Cloud Gateway) and a `discovery-service` (Eureka). The DevOps team prefers Istio for traffic management and security.

## Options considered
- Keep Spring Cloud Gateway + Eureka: runs anywhere, team knows it, routing/filters in Java; another service to scale and secure.
- Move to Istio (service mesh/ingress gateway): mTLS between services, traffic policy, observability from the platform; requires Kubernetes and DevOps ownership; Eureka may become unnecessary.
- Hybrid: Istio at the edge, services keep JWT validation and Eureka until migration finishes.

## Decision
TODO: pending agreement with DevOps.

## Consequences (to evaluate)
- Where JWT validation happens at the edge (service-side validation remains mandatory either way).
- What replaces Eureka lookups (`lb://` URIs) if discovery moves to Kubernetes DNS.
- What happens to the `api-gateway` folder and its CLAUDE.md/rules.
