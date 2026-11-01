# Cross-service change rules

## One service per task
- A task changes one service. If another service must change, STOP and write a note for its owner: what, why, contract file, deadline. Do not edit that service.

## Order of a spanning change
1. Contract PR, 2. provider, 3. consumers, 4. cleanup of the old version.
- A consumer keeps working against the previous contract version until the provider's new version is deployed (expand/contract).
- New event: contract, then publisher, then consumers. Removing an event: stop consumers, then publisher, then delete the contract.

## Data and code boundaries
- Database changes stay in the owning service: no shared tables, no cross-service foreign keys, no direct SQL access.
- No shared libraries or DTO jars between services; share contracts, not code.

## Configuration
- Shared values (ports, hostnames, queue names) are documented in `docs/service-map.md` and set in config-server, not copied into code.

## Ownership
- Owners (CODEOWNERS) approve changes to their contracts and migrations.

Example: project-service needs a new field from user-service. Request the contract change (additive), write a note for Vannda, and keep working against v1 meanwhile.
