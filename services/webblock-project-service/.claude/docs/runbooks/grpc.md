# gRPC server runbook

The Project service runs one gRPC server in the same process as the HTTP server (FR-31, TR-24, decision 0011). It
serves `webblock.project.v1.AccessService/CheckAccess`, the access check Provisioning calls. The REST check
`POST /internal/v1/access-checks` gives the same answers and stays until FR-34.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `webblock.grpc.port` | `9090` | The port the server listens on. The Kubernetes Service names it `grpc`. `0` takes a free port (tests only). |
| `webblock.grpc.shutdown-grace` | `10s` | At stop, calls in flight may finish for this long; then they are cut. |
| `webblock.grpc.max-calls-in-flight` | `8` | Calls that may run at once. More answer `UNAVAILABLE`, reason `DEPENDENCY_UNAVAILABLE`. Keep it below the project database pool (10), which REST shares. A call takes its place only at half-close, when its handler is about to run; a call started but not half-closed holds none. The cap covers unary calls, the only kind Project serves. |

All three are in `src/main/resources/application.yml`. An environment variable overrides them
(`WEBBLOCK_GRPC_PORT`, `WEBBLOCK_GRPC_SHUTDOWNGRACE`, `WEBBLOCK_GRPC_MAXCALLSINFLIGHT`). The server starts in every
profile.

Fixed in code (`common/presentation/grpc/GrpcServer`): virtual threads, an inbound message of at most 64 KiB and
metadata of at most 16 KiB, no reflection service and no health service. It serves only services in a
`presentation.grpc` package of this service and refuses to start on any other. Netty limits each connection (100
calls at once, 5 minutes idle, 30 minutes old, client pings, stream resets). A method it does not serve answers
`UNIMPLEMENTED`, reason `ROUTE_NOT_FOUND`, without the path. A busy database (lock wait, statement timeout, no free
pooled connection) answers `UNAVAILABLE`, reason `DEPENDENCY_UNAVAILABLE`. The values, the gRPC status of each error
code, and the `ErrorInfo` every error carries are in decision 0011.

The proto comes from `web-block-contracts` at the commit in `contracts.lock`. Only `scripts/contracts-sync.sh` writes
`contracts/`; `scripts/contracts-sync.sh --check` fails on any difference.

## Who may call it

The gRPC port reads no token and no `x-user-id` metadata. Its only gate is the mesh: STRICT mTLS, and Project's
Istio `AuthorizationPolicy`, which admits one caller on one port, one method and one path:

- caller `cluster.local/ns/webblock/sa/provisioning-api`
- port `9090`, method `POST`, path `/webblock.project.v1.AccessService/CheckAccess`

The subject of the check is the `user_id` field of the request. Without the policy, every pod in the mesh can ask
about any user.

## Mesh changes for the owner (in `web-block-config`)

Agents never apply these. The owner adds them to `web-block-config` and applies them the way that repository is
applied.

1. Project's Deployment and Service: port 9090 named `grpc`, so Istio treats the traffic as gRPC. Without the name,
   Istio sees plain TCP, a rule with `paths` never matches, and every call is refused. The Service stays `ClusterIP`:
   no NodePort, and no Ingress or gateway route to 9090.

   ```yaml
   # Deployment, container ports
   ports:
     - name: grpc
       containerPort: 9090
   # Service
   spec:
     type: ClusterIP
     ports:
       - name: grpc
         port: 9090
         targetPort: grpc
   ```

2. Keep STRICT `PeerAuthentication` for Project, with no `portLevelMtls` override for 9090.

3. Project's `AuthorizationPolicy`: a separate rule for gRPC. Never add Provisioning's principal or this path to the
   rule for `POST /internal/v1/access-checks`: that rule also admits the tenant namespaces `wb-*` (decision 0002).
   Keep that rule until FR-34.

   ```yaml
   - from:
       - source:
           principals: ["cluster.local/ns/webblock/sa/provisioning-api"]
     to:
       - operation:
           ports: ["9090"]
           methods: ["POST"]
           paths: ["/webblock.project.v1.AccessService/CheckAccess"]
   ```

4. Pin every other ALLOW rule of Project's policy to its port with `to.operation.ports`: `8080` for `/api/*` and
   `/internal/*`, `8081` for Prometheus. A rule with a source and no port or path lets that source reach 9090.

5. Kind: Kind has no Istio yet, so port 9090 is open to every pod there, as `/internal/**` is. If the Kind CNI
   enforces NetworkPolicy, a policy that admits 9090 only from Provisioning's namespace closes the gap meanwhile.

A portal caller is added only when its identity is decided.

## Check

- Locally (`SPRING_PROFILES_ACTIVE=prototype ./gradlew bootRun`), the log shows
  `gRPC server listening on port 9090, serving [webblock.project.v1.AccessService], at most 8 calls at once`.
- A call with `grpcurl` (a separate tool), from the repository root:

  ```sh
  grpcurl -plaintext -import-path contracts/proto -proto webblock/project/v1/access.proto \
    -d '{"user_id":"<user uuid>","project_id":"<project uuid>","action":"get_release"}' \
    localhost:9090 webblock.project.v1.AccessService/CheckAccess
  ```

  An unknown project answers `{"reason": "NOT_FOUND"}` with status OK; `allowed` is false, so it is left out.
- In the mesh, after the owner applies the policy: a call from `provisioning-api` gets an answer, a call from any
  other workload, or to any other port or path, is refused by Istio before it reaches the service.
