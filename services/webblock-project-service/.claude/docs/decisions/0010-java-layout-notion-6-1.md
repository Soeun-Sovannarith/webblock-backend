# 0010 Java layout of Notion 6.1

## Decision

- Each feature module has four layers: `presentation`, `application`, `domain`, `infrastructure`. Every class sits
  in one type folder of its layer, as `TYPE_FOLDERS` in `ArchitectureRulesTest` lists them: presentation
  `controller`, `grpc`; application `service`, `model`, `port`, `job`, `config`, `request`, `response`,
  `validation`; domain `entity`, `model`; infrastructure `config`, `persistence`, `client`, `messaging`, `standin`.
  `common` keeps its own folders (presentation `envelope`, `error`, `filter`, `security`; domain `error`, `support`;
  infrastructure `security`) and its validation annotations sit in `common/application/validation`.
- Request, response and validation types sit in `<feature>/application/request`, `response` and `validation`.
  These packages have no `@NamedInterface`, so Spring Modulith refuses their use by another feature.
- A change of the type folders changes `TYPE_FOLDERS` in the same commit. No rule is loosened.
- REST mapping stays in static factories (`ProjectView.of`) and `toCommand`. There is no `mapper` folder; one comes
  with its first class. Command and model records stay, and the application services keep their signatures.
- `ProjectView` stays in `project/application/model`: it is the project module's one representation of a website,
  used by the project and the wizard controllers. It is a model of the module's API, not a per-feature response.
- The gRPC edge is presentation. gRPC services and their mappers sit in `<feature>/presentation/grpc`; the server,
  its interceptors and the error mapping sit in `common/presentation/grpc`. Every `BindableService` implementation
  sits in a `presentation.grpc` package, and only `presentation.grpc` classes use `io.grpc` or the generated
  `webblock..` types (two ArchUnit rules).
- Classes generated from protos live outside the base package (`webblock.project.v1`).

## Reason

Notion "Web Block architecture" 6.1 puts request, response and mapping types in `application/` and gRPC servers in
`presentation`; the code kept them in `presentation/request` and `presentation/response`. Moving them makes the
layout match the source of truth, and Art. III states the new place. Validation annotations move with the requests,
since application may not use presentation (`applicationDoesNotUsePresentation`).

Options considered: keep the old folders (lasting drift from 6.1); pass request records into the services (a larger
change with no FR); a REST mapper class per feature (it would only copy the factories); a wizard copy of
`ProjectView` (ten copied fields). The cost if wrong is another package move, which changes imports only. Placing
the gRPC server in infrastructure would break `infrastructureDoesNotUsePresentation`.

## Status

Superseded by `0016`: the layer-first layout replaces the feature-first folders and the Modulith named interfaces
above. The mapping rule (static factories, `toCommand`) and the gRPC edge in `presentation/grpc` carry over into
`0016`.
