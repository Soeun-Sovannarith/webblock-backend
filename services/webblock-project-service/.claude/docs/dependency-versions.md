# Dependency versions

Rule: every version is at least 14 days old when it is added. The gRPC and protobuf pins are the ones
`web-block-commons` pins (equal to the Spring Boot 4.1.1 BOM where it manages them), so both libraries run one
version. Dates are the publication dates seen on the repository (Last-Modified of the artifact, or the Gradle
`buildTime`).

The owner approved the gRPC versions: grpc-java 1.83.1, protobuf 4.35.1, proto-google-common-protos 2.78.0, the
protobuf Gradle plugin 0.10.0 and guava 33.7.2-android.

| Item | Version | Published | Source URL |
| --- | --- | --- | --- |
| Gradle distribution | 9.8.0 | 2026-09-24 | https://services.gradle.org/distributions/gradle-9.8.0-bin.zip (9.8.1 of 2026-10-07 is too new) |
| `org.springframework.boot` plugin / Spring Boot BOM | 4.1.1 | 2026-08-20 | https://repo.maven.apache.org/maven2/org/springframework/boot/spring-boot/4.1.1/ |
| `io.spring.dependency-management` | 1.1.7 | 2024-12-17 | https://repo.maven.apache.org/maven2/io/spring/gradle/dependency-management-plugin/1.1.7/ |
| Spring Modulith BOM (`spring-modulith-starter-core`, `-starter-namastack` for the outbox; no Modulith test artifact) | 2.1.1 | 2026-08-25 | https://repo.maven.apache.org/maven2/org/springframework/modulith/spring-modulith-bom/2.1.1/ |
| `com.diffplug.spotless` | 8.10.3 | 2026-09-25 | https://repo.maven.apache.org/maven2/com/diffplug/spotless/spotless-plugin-gradle/8.10.3/ (8.10.4 of 2026-10-08 is too new) |
| palantir-java-format | 2.100.0 | 2026-09-28 | https://repo.maven.apache.org/maven2/com/palantir/javaformat/palantir-java-format/2.100.0/ |
| `net.ltgt.errorprone` | 5.1.1 | 2026-08-25 | https://plugins.gradle.org/m2/net/ltgt/gradle/gradle-errorprone-plugin/5.1.1/ |
| `com.google.errorprone:error_prone_core` | 2.50.0 | 2026-06-10 | https://repo.maven.apache.org/maven2/com/google/errorprone/error_prone_core/2.50.0/ |
| Security override: `com.google.guava:guava` (only the annotation processor classpaths `annotationProcessor`, `testAnnotationProcessor` and `contractsAnnotationProcessor`, which carry Error Prone; error_prone_core 2.50.0 declares 33.5.0-jre; pinned by a resolution rule in `build.gradle` that only raises a request up to the pin and keeps a later one, version `guava-errorprone` in `gradle/libs.versions.toml`) | 33.7.2-jre | 2026-09-29 | https://repo.maven.apache.org/maven2/com/google/guava/guava/33.7.2-jre/ (fixes GHSA-xxph-c9ww-hj94, High; Apache-2.0; newest release; never on an application classpath) |
| `com.tngtech.archunit:archunit-junit5` | 1.5.1 | 2026-09-25 | https://repo.maven.apache.org/maven2/com/tngtech/archunit/archunit-junit5/1.5.1/ |
| `com.fasterxml.uuid:java-uuid-generator` | 5.2.0 | 2025-12-05 | https://repo.maven.apache.org/maven2/com/fasterxml/uuid/java-uuid-generator/5.2.0/ |
| Spring Modulith Namastack starter (`spring-modulith-starter-namastack`, from the Modulith BOM) | 2.1.1 | 2026-08-25 | https://repo.maven.apache.org/maven2/org/springframework/modulith/spring-modulith-starter-namastack/2.1.1/ |
| Namastack Outbox BOM (`io.namastack:namastack-outbox-bom`; `-api`, `-rabbit`, and through the starter `-core`, `-jdbc`, `-jackson`, `-starter-jdbc`) | 1.7.3 | 2026-08-20 | https://repo.maven.apache.org/maven2/io/namastack/namastack-outbox-bom/1.7.3/ (the version `spring-modulith-events-namastack-2.1.1.pom` and `spring-modulith-starter-namastack-2.1.1.pom` declare; 1.8.1 of 2026-08-20 and 1.9.0 of 2026-08-28 are also eligible but not the version Modulith 2.1.1 is built against) |
| Kotlin runtime (`kotlin.version`, overrides the Spring Boot BOM's 2.3.21) | 2.4.10 | 2026-07-14 | https://repo.maven.apache.org/maven2/org/jetbrains/kotlin/kotlin-stdlib/2.4.10/ (Namastack 1.7.3 is compiled with Kotlin 2.4, metadata 2.4, and declares kotlin-stdlib 2.4.10) |
| Security override: `tomcat.version` (tomcat-embed-core, -el, -websocket; BOM manages 11.0.24) | 11.0.25 | 2026-08-12 | https://repo.maven.apache.org/maven2/org/apache/tomcat/embed/tomcat-embed-core/11.0.25/ (fixes CVE-2026-65905, CVE-2026-65182, CVE-2026-68525) |
| Security override: `jackson-bom.version` (jackson-bom, jackson-core, jackson-databind; BOM manages 3.1.5) | 3.1.7 | 2026-09-21 (databind 2026-09-22) | https://repo.maven.apache.org/maven2/tools/jackson/jackson-bom/3.1.7/ (fixes 7 GHSA advisories) |
| Security override: `rabbit-amqp-client.version` (`com.rabbitmq:amqp-client`; BOM manages 5.30.0) | 5.37.0 | 2026-10-01 | https://repo.maven.apache.org/maven2/com/rabbitmq/amqp-client/5.37.0/ (fixes 10 GHSA advisories) |
| `com.networknt:json-schema-validator` (3.0.x line uses Jackson 3) | 3.0.8 | 2026-09-30 | https://repo.maven.apache.org/maven2/com/networknt/json-schema-validator/3.0.8/ (exactly 14 days old on 2026-10-14) |
| `io.github.erdtman:java-json-canonicalization` (RFC 8785 JCS) | 1.1 | 2018-08-18 | https://repo.maven.apache.org/maven2/io/github/erdtman/java-json-canonicalization/1.1/ (newest release; `maven-metadata.xml` lists only 1.0 and 1.1) |
| `org.wiremock:wiremock-standalone` (test only) | 3.13.2 | 2025-11-14 | https://repo.maven.apache.org/maven2/org/wiremock/wiremock-standalone/3.13.2/ (newest 3.13.x; 4.0.0 betas not used) |
| `spring-boot-starter-data-jpa-test` | 4.1.1 | managed | Spring Boot 4.1.1 BOM; no version of our own |
| `io.grpc:grpc-api`, `grpc-stub`, `grpc-protobuf`, `grpc-netty-shaded`, `grpc-inprocess` (tests only) | 1.83.1 | 2026-07-30 | https://repo.maven.apache.org/maven2/io/grpc/grpc-api/1.83.1/ (Apache-2.0; `grpc-java.version` of the Spring Boot 4.1.1 BOM and the version `web-block-commons` pins; one version for every grpc-java artifact) |
| `io.grpc:protoc-gen-grpc-java` (build tool, `protobufToolsLocator_grpc`) | 1.83.1 | 2026-07-30 | https://repo.maven.apache.org/maven2/io/grpc/protoc-gen-grpc-java/1.83.1/ (Apache-2.0; equal to the grpc-java artifacts) |
| `com.google.protobuf:protobuf-java` | 4.35.1 | 2026-06-11 | https://repo.maven.apache.org/maven2/com/google/protobuf/protobuf-java/4.35.1/ (BSD-3-Clause; `protobuf-java.version` of the Spring Boot 4.1.1 BOM; the `web-block-commons` pin) |
| `com.google.protobuf:protoc` (build tool, `protobufToolsLocator_protoc`) | 4.35.1 | 2026-06-11 | https://repo.maven.apache.org/maven2/com/google/protobuf/protoc/4.35.1/ (BSD-3-Clause; equal to protobuf-java) |
| `com.google.api.grpc:proto-google-common-protos` (`google.rpc.ErrorInfo`, `BadRequest`) | 2.78.0 | 2026-09-30 | https://repo.maven.apache.org/maven2/com/google/api/grpc/proto-google-common-protos/2.78.0/ (Apache-2.0; not managed by the BOM; the `web-block-commons` pin; grpc-protobuf 1.83.1 declares 2.64.1) |
| `com.google.protobuf` Gradle plugin | 0.10.0 | 2026-04-20 | https://plugins.gradle.org/m2/com/google/protobuf/com.google.protobuf.gradle.plugin/0.10.0/ (BSD-3-Clause; the `web-block-commons` pin) |
| Security override: `com.google.guava:guava` on every classpath but the annotation processor ones (gRPC 1.83.1 declares 33.6.0-android; pinned by the resolution rule in `build.gradle`, which never lowers a later request, version `guava-grpc` in `gradle/libs.versions.toml`) | 33.7.2-android | 2026-09-29 | https://repo.maven.apache.org/maven2/com/google/guava/guava/33.7.2-android/ (Apache-2.0; fixes GHSA-xxph-c9ww-hj94, High; the `web-block-commons` override) |
| `org.springdoc:springdoc-openapi-starter-webmvc-ui` (with `-webmvc-api` and `-common`; OpenAPI description and Swagger UI of `/api/v1`, on only in the `prototype` profile, TR-40) | 3.1.1 | 2026-09-06 | https://repo.maven.apache.org/maven2/org/springdoc/springdoc-openapi-starter-webmvc-ui/3.1.1/ (Apache-2.0; owner-approved; built against Spring Boot 4.1.0; 3.2.0 of 2026-10-24 is too new) |
| Through springdoc 3.1.1: `io.swagger.core.v3:swagger-core-jakarta`, `-models-jakarta`, `-annotations-jakarta` | 2.2.55 | 2026-08-31 | https://repo.maven.apache.org/maven2/io/swagger/core/v3/swagger-core-jakarta/2.2.55/ (Apache-2.0; the version springdoc 3.1.1 declares) |
| Through springdoc 3.1.1: `org.webjars:swagger-ui` | 5.32.14 | 2026-08-19 | https://repo.maven.apache.org/maven2/org/webjars/swagger-ui/5.32.14/ (Apache-2.0) |
| Through springdoc 3.1.1: `org.webjars:webjars-locator-lite` | 1.1.4 | 2026-07-15 | https://repo.maven.apache.org/maven2/org/webjars/webjars-locator-lite/1.1.4/ (MIT) |
| Through springdoc 3.1.1 (swagger-core 2.2.55): `org.apache.commons:commons-lang3`, on the production runtime classpath | 3.20.0 | 2025-11-12 | https://repo.maven.apache.org/maven2/org/apache/commons/commons-lang3/3.20.0/ (Apache-2.0; after CVE-2025-48924, fixed in 3.18.0; OSV clean) |
| `jakarta.xml.bind:jakarta.xml.bind-api` (Boot BOM, through Hibernate on the production runtime classpath; swagger-core 2.2.55 also puts it on the compile classpath) | 4.0.5 | 2026-02-06 | https://repo.maven.apache.org/maven2/jakarta/xml/bind/jakarta.xml.bind-api/4.0.5/ (EDL 1.0; OSV clean) |
| `jakarta.activation:jakarta.activation-api` (Boot BOM, through jakarta.xml.bind-api on the production runtime classpath; also on the compile classpath through swagger-core) | 2.1.4 | 2025-09-11 | https://repo.maven.apache.org/maven2/jakarta/activation/jakarta.activation-api/2.1.4/ (EDL 1.0; OSV clean) |
| Security override: `jackson-2-bom.version` (Jackson 2: `com.fasterxml.jackson.core:jackson-core`, `-databind`, `jackson-dataformat-yaml`, `jackson-datatype-jsr310`; only through springdoc's swagger-core 2.2.55, which declares 2.22.1; BOM manages 2.21.5) | 2.21.7 | 2026-09-21 | https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-databind/2.21.7/ (Apache-2.0; fixes GHSA-7hhh-6rmp-j9qf, GHSA-p6pp-m3f8-5c89, GHSA-cxp5-3px4-pw24, GHSA-gx83-3vf8-gh7j, GHSA-q4xh-88c3-wmh7, GHSA-wjgm-6hv5-3cvf and GHSA-wv8q-qhhj-9h54 in 2.21.5; owner-approved; swagger-core serialises the description with Jackson 2, the service's own JSON stays on Jackson 3) |

## Tools

Dev tools only, never on an application classpath (decision 0009). Rule: published at least 14 days before they
were added (cutoff 2026-10-14). Their own libraries resolve at the versions the tools declare, are locked in
`gradle.lockfile` (configurations `pmd`, `cpd`, `spotbugs`, `spotbugsSlf4j`) and scanned by `scripts/osv-scan.sh`.

| Tool | Version | Published | Source |
| --- | --- | --- | --- |
| osv-scanner (linux amd64) | v2.6.0 | 2026-09-14 | https://github.com/google/osv-scanner/releases/tag/v2.6.0 (SHA-256 `ca69b3d3...85b108` from the release's `osv-scanner_SHA256SUMS`, pinned in `scripts/osv-scan.sh`; v2.7.0 of 2026-10-12 is too new) |
| PMD (`net.sourceforge.pmd:pmd-java`, `pmd-ant`; Gradle `pmd` plugin `toolVersion`) | 7.28.0 | 2026-09-25 | https://repo.maven.apache.org/maven2/net/sourceforge/pmd/pmd-java/7.28.0/ (newest release; BSD-style licence) |
| `org.slf4j:slf4j-simple` (only in the `cpd` configuration; brings `slf4j-api` 1.7.36) | 1.7.36 | 2022-02-08 | https://repo.maven.apache.org/maven2/org/slf4j/slf4j-simple/1.7.36/ (the SLF4J version PMD 7.28.0 declares and ships; MIT) |
| SpotBugs (`com.github.spotbugs:spotbugs`) | 4.10.5 | 2026-10-12 | https://repo.maven.apache.org/maven2/com/github/spotbugs/spotbugs/4.10.5/ (newest release; LGPL-2.1) |
| SpotBugs Gradle plugin (`com.github.spotbugs`) | 6.5.13 | 2026-10-12 | https://plugins.gradle.org/m2/com/github/spotbugs/snom/spotbugs-gradle-plugin/6.5.13/ (newest release; Apache-2.0) |

## Managed by the Spring Boot 4.1.1 BOM

Source: `spring-boot-dependencies-4.1.1.pom`. Testcontainers 2.0.5, Flyway 12.4.0, Awaitility 4.3.0, plus JUnit,
Jackson,
Hibernate and the PostgreSQL driver.

## Starter and module names checked against Spring Boot 4.1.1 and Testcontainers 2.0.5

Each artifact was confirmed to exist on Maven Central. No renames were needed.

- `spring-boot-starter-webmvc`, `-validation`, `-security`, `-oauth2-resource-server`, `-data-jpa`, `-flyway`, `-amqp`,
  `-actuator`
- `spring-boot-starter-test`, `-webmvc-test`, `-security-test`, `spring-boot-testcontainers`
- `org.testcontainers:testcontainers-junit-jupiter`, `testcontainers-postgresql`, `testcontainers-rabbitmq`

## Waivers

Age-rule waivers (the 14-day rule above). Each expires; remove it and re-check on the expiry date.

None open.

Kotlin runtime 2.4.10 was published 2026-07-14, before the 2026-09-29 cutoff, so it satisfies the age rule and needs no
waiver.
It is deliberately not the newest eligible Kotlin: 2.4.20 (2026-09-07) also passes the age rule, but Namastack Outbox
1.7.3
is built against and declares kotlin-stdlib 2.4.10, so 2.4.10 is the runtime it was compiled and tested with (binary
compatibility with Namastack 1.7.3). Move it only together with a Namastack upgrade.

## Verification exception: IDE sources and javadoc

`gradle/verification-metadata.xml` trusts files named `*-sources.jar` and `*-javadoc.jar` without a checksum (owner's
decision). IDEs such as IntelliJ download them on import to show library source; they are never on the compile or
runtime classpath, so every jar the build uses is still verified by SHA-256. Without this, a Gradle import in the IDE
fails with "Dependency verification failed ... -sources.jar".
