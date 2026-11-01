# Project technology stack

What this service uses, with exact versions. Sources: `gradle/libs.versions.toml`, `build.gradle`,
`gradle.lockfile`, `gradle/wrapper/gradle-wrapper.properties` and `scripts/osv-scan.sh`. Publication dates and the
14-day rule: `.claude/docs/dependency-versions.md`. Every dependency is open source and locked
(`gradle.lockfile`, `gradle/verification-metadata.xml`).

## Language and build

| Item | Version |
| --- | --- |
| Java (Gradle toolchain) | 25 |
| Gradle (wrapper) | 9.8.0 |
| Spring Boot Gradle plugin | 4.1.1 |
| io.spring.dependency-management | 1.1.7 |
| com.google.protobuf Gradle plugin, protoc, protoc-gen-grpc-java | 0.10.0, 4.35.1, 1.83.1 |

## Runtime libraries

| Library | Version | Used for |
| --- | --- | --- |
| Spring Boot (webmvc, validation, security, oauth2-resource-server, data-jpa, flyway, amqp, actuator) | 4.1.1 | The service |
| Spring Framework | 7.0.9 | Through Spring Boot |
| Spring Security (oauth2-resource-server) | 7.1.1 | JWT check: RS256, issuer, audience `project` |
| Spring Data JPA | 4.1.1 | Repositories in `infrastructure/persistence` |
| Hibernate ORM | 7.4.5.Final | JPA; `ddl-auto: validate` |
| Flyway (core, database-postgresql) | 12.4.0 | Migrations of both databases |
| PostgreSQL JDBC | 42.7.13 | Driver |
| Spring AMQP (spring-rabbit) | 4.1.1 | RabbitMQ |
| RabbitMQ amqp-client | 5.37.0 | Security override of the Boot BOM |
| Spring Modulith (core, namastack starter) | 2.1.1 | Event externalization through the outbox (features are not modules) |
| Namastack Outbox (api, rabbit) | 1.7.3 | Transactional outbox to RabbitMQ |
| Kotlin stdlib | 2.4.10 | Runtime Namastack needs |
| Jackson | 3.1.7 | JSON; security override of the Boot BOM |
| Tomcat embed | 11.0.25 | Servlet container; security override of the Boot BOM |
| Micrometer Prometheus registry | 1.17.1 | `/actuator/prometheus` |
| java-uuid-generator | 5.2.0 | UUIDv7 IDs |
| json-schema-validator (networknt) | 3.0.8 | Puck tree checks against the component catalog |
| grpc-java (grpc-api, grpc-stub, grpc-protobuf, grpc-netty-shaded) | 1.83.1 | The CheckAccess gRPC server (decision 0011) |
| protobuf-java | 4.35.1 | Messages of the stubs generated from `contracts/proto` (source set `contracts`) |
| proto-google-common-protos | 2.78.0 | `google.rpc.ErrorInfo` and `BadRequest` in gRPC errors |
| Guava (through grpc-java) | 33.7.2-android | Security override of gRPC's 33.6.0-android (GHSA-xxph-c9ww-hj94) |
| java-json-canonicalization | 1.1 | RFC 8785 hashes of captured input |
| springdoc-openapi (starter-webmvc-ui) | 3.1.1 | OpenAPI description of `/api/v1` and Swagger UI, `prototype` profile only (TR-40) |
| swagger-core (jakarta), swagger-ui webjar | 2.2.55, 5.32.14 | Through springdoc |
| Jackson 2 (core, databind, yaml, jsr310) | 2.21.7 | Through swagger-core only; security override of the Boot BOM; the service's JSON uses Jackson 3 |

## What it connects to

| System | How |
| --- | --- |
| PostgreSQL 18 database `webblock_project` (CloudNativePG cluster `platform-db`) | Schemas `project`, `project_messaging`, `project_ops`; roles `project_app`, `project_migrator` |
| Shared catalog database (cluster `catalog-db`) | Separate pool; migrations in `db/migration/catalog/`; role `catalog_owner` migrates |
| RabbitMQ 4 | Topic exchange `project.events` with alternate exchange `project.events.unrouted`; publisher confirms |
| Keycloak (realm `webblock`) | Key set over HTTPS; audience `project`; claim `user_id` |
| Payment | HTTP admission client (`infrastructure/client/payment`); stand-in in the prototype profile |
| Provisioning, AI Codegen | Call `/internal/v1/...`; no token, the Istio mesh policy is the gate |
| web-block-contracts | `scripts/contracts-sync.sh` copies the protos at the commit in `contracts.lock` into `contracts/` |

## Test and quality tools

| Tool | Version | Gate |
| --- | --- | --- |
| JUnit Jupiter | 6.0.3 | `test`, `integrationTest` |
| AssertJ | 3.27.7 | every test |
| Mockito | 5.23.0 | `@MockitoBean` at slice boundaries |
| Testcontainers | 2.0.5 | `postgres:18` and `rabbitmq:4.3.6-management-alpine` in `integrationTest` |
| ArchUnit (junit5) | 1.5.1 | `architecture/ArchitectureRulesTest` in `test` |
| WireMock standalone | 3.13.2 | Payment HTTP client tests |
| grpc-inprocess | 1.83.1 | in-process gRPC server in tests |
| Awaitility | 4.3.0 | async waits |
| JaCoCo | 0.8.15 | `jacocoTestReport`, `jacocoTestCoverageVerification` (in `check`) |
| Spotless plugin, palantir-java-format | 8.10.3, 2.100.0 | `spotlessCheck` (in `check`) |
| Error Prone plugin, error_prone_core | 5.1.1, 2.50.0 | every `compileJava`; guava on its classpath pinned to 33.7.2-jre (GHSA-xxph-c9ww-hj94) |
| osv-scanner | v2.6.0 | `scripts/osv-scan.sh` on `gradle.lockfile` (checksum-verified) |
| PMD (Gradle `pmd` plugin), rules in `config/pmd/ruleset.xml` | 7.28.0 | `pmdMain`, `pmdTest`: report-only, not in `check` (decision 0009) |
| CPD (from PMD, with slf4j-simple 1.7.36) | 7.28.0 | `cpd`: duplicate main code, 100 tokens, report-only |
| SpotBugs, SpotBugs Gradle plugin | 4.10.5, 6.5.13 | `spotbugsMain`: effort max, threshold medium, report-only, not in `check` |
| javac `-Xlint:deprecation,removal` | JDK 25 | every compile; warnings only |
