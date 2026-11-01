# 0016 Layer-first Java layout of Notion 6.1

## Decision

- The Java code follows the tree of Notion "Web Block architecture" 6.1: the four layers are the top packages under
  `org.kshrd.webblock.project`, and each layer is split by type folder and feature, in this order:
  - `presentation/rest/<feature>` (REST controllers), `presentation/grpc/<feature>` (gRPC services and their
    mappers), `presentation/messaging/<feature>` (message listeners);
  - `application/<feature>/{handler,request,response,validation,model,port,job,config}`; the use cases sit in
    `handler`;
  - `domain/<feature>/model` (entities with `@Entity`, enums and value rules);
  - `infrastructure/{persistence,config,client,messaging,standin}/<feature>`.
- The owner's ruling overrides the last sentence of Notion 6.1 ("Project ... keeps that layout"). Beyond the 6.1
  drawing: `presentation/messaging` and the `infrastructure` type folders get feature folders, and the domain has
  only `domain/<feature>/model` (no `constant` or `event` folder until a class needs one).
- `common` keeps its own layout: `common/presentation`, `common/application`, `common/domain` and
  `common/infrastructure`, each with its type folders. Notion 6.1 shows `common` as the folder every feature shares.
- `TYPE_FOLDERS` in `ArchitectureRulesTest` lists the type folders above; `COMMON_TYPE_FOLDERS` lists common's. A
  change of the folders changes them in the same commit.
- Features are not Spring Modulith modules: there is no `@ApplicationModule`, no `@NamedInterface` and no
  `ModuleStructureTest`. The ArchUnit rules state the direction of the layers (presentation never uses
  infrastructure or a feature's domain, application and infrastructure never use presentation, the domain uses no
  other layer and only the allowed libraries) and the gRPC, envelope, method security and stand-in rules. There is
  no per-feature isolation rule. Spring Modulith stays for the event externalization of the outbox (decision `0001`).
- The application services keep their classes and names (`ProjectService` and the others) in `handler` until a
  later task splits them into one handler per use case, as Notion 6.1 shows.
- The migration job's main class is `org.kshrd.webblock.project.infrastructure.persistence.migration.Migrations`.
- The gRPC server serves a `BindableService` only from a feature folder of `presentation/grpc` or from
  `common/presentation/grpc`.
- Request, response and mapping stay per feature; mapping stays in static factories (`ProjectView.of`) and
  `toCommand` (decision `0010`, unchanged in that part).

## Reason

Notion 6.1 shows a layer-first tree for the Java services. The owner approved moving Project to it and ruled that
the features are not Modulith modules and that the services stay whole until the handler split. The move changes
packages and imports only; behaviour, endpoints, events and the databases do not change.

Options considered: keep the feature-first tree (lasting drift from the 6.1 tree the other Java services use); keep
Modulith modules inside the layer-first tree (a feature would have no single package to be a module, so every type
folder would need its own named interface); split the services into handlers in the same change (a larger change
with no FR, kept for a later task). The cost if wrong is another package move.

## Status

Accepted. Built: the layer-first tree, `TYPE_FOLDERS` and `COMMON_TYPE_FOLDERS` in `ArchitectureRulesTest`, the
layout rule tests in `LayoutRuleTest`, the gRPC server's package check, and the coverage rules in `build.gradle`
(`application.*.handler.*Service`, `domain.*.model.*Entity`). Supersedes `0010` for the layout; the handler split
is open.
