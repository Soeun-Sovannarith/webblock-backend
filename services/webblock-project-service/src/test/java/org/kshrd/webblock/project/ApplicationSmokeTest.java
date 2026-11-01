package org.kshrd.webblock.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import io.namastack.outbox.OutboxPayloadSerializer;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.Filter;
import jakarta.servlet.ServletException;
import java.io.ByteArrayInputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Instant;
import java.util.EnumSet;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.postgresql.util.PSQLException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.health.actuate.endpoint.HealthEndpointGroups;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.boot.test.web.server.LocalManagementPort;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.core.Ordered;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.web.client.RestClient;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.rabbitmq.RabbitMQContainer;

@Tag("integration")
@ActiveProfiles("prototype")
@Testcontainers
@ExtendWith(OutputCaptureExtension.class)
@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"management.server.port=0", "webblock.grpc.port=0", "webblock.http.max-body-bytes=2048"})
class ApplicationSmokeTest {

    /** Any bearer token is a valid user; a filter that fails after the request ID exists but before security. */
    @TestConfiguration(proxyBeanMethods = false)
    static class Beans {

        @Bean
        FilterRegistrationBean<Filter> containerFailure() {
            FilterRegistrationBean<Filter> registration = new FilterRegistrationBean<>((request, response, chain) -> {
                throw new ServletException("secret container failure");
            });
            registration.addUrlPatterns("/api/test-container-error");
            registration.setDispatcherTypes(EnumSet.of(DispatcherType.REQUEST));
            registration.setOrder(Ordered.HIGHEST_PRECEDENCE + 20);
            return registration;
        }
    }

    /** Overrides the real jwtDecoder bean (and fails if it disappears): any bearer token is a valid user. */
    @MockitoBean(name = "jwtDecoder", enforceOverride = true)
    JwtDecoder jwtDecoder;

    @BeforeEach
    void anyTokenIsAUser() {
        when(jwtDecoder.decode(anyString()))
                .thenAnswer(invocation -> Jwt.withTokenValue(invocation.getArgument(0))
                        .header("alg", "none")
                        .subject("tester")
                        .claim("user_id", "0199e0a2-0000-7000-8000-0000000000aa")
                        .issuedAt(Instant.now())
                        .expiresAt(Instant.now().plusSeconds(60))
                        .build());
    }

    @Autowired
    OutboxPayloadSerializer payloadSerializer;

    @Autowired
    HealthEndpointGroups healthGroups;

    @Autowired
    JdbcClient jdbc;

    @Container
    @ServiceConnection
    static RabbitMQContainer rabbit = new RabbitMQContainer("rabbitmq:4.3.6-management-alpine");

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry registry) throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase(false);
        registry.add("spring.datasource.url", db::url);
        registry.add("spring.datasource.username", () -> PostgresFixture.APP_LOGIN);
        registry.add("spring.datasource.password", () -> PostgresFixture.PASSWORD);
        registry.add("webblock.security.identity-mode", () -> "jwt");
        registry.add("spring.flyway.url", db::url);
        registry.add("spring.flyway.user", () -> PostgresFixture.MIGRATOR_LOGIN);
        registry.add("spring.flyway.password", () -> PostgresFixture.PASSWORD);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        registry.add("webblock.catalog.datasource.url", catalog::url);
        registry.add("webblock.catalog.datasource.username", () -> PostgresFixture.CATALOG_PLATFORM_LOGIN);
        registry.add("webblock.catalog.datasource.password", () -> PostgresFixture.PASSWORD);
        registry.add("webblock.catalog.migrations.user", () -> PostgresFixture.CATALOG_MIGRATOR_LOGIN);
        registry.add("webblock.catalog.migrations.password", () -> PostgresFixture.PASSWORD);
    }

    @LocalServerPort
    int port;

    @LocalManagementPort
    int managementPort;

    private record Reply(int status, String body) {}

    private Reply get(int onPort, String path) {
        return RestClient.create("http://localhost:" + onPort)
                .get()
                .uri(path)
                .exchange((request, res) -> new Reply(res.getStatusCode().value(), res.bodyTo(String.class)));
    }

    @Test
    void startsMigratesAndReportsReady() {
        Reply reply = get(managementPort, "/actuator/health/readiness");
        assertThat(reply.status()).isEqualTo(200);
        assertThat(reply.body()).contains("\"status\":\"UP\"");
    }

    @Test
    void healthIsPublicAndNotWrappedInTheEnvelope() {
        Reply reply = get(managementPort, "/actuator/health");
        assertThat(reply.status()).isEqualTo(200);
        assertThat(reply.body()).contains("\"status\":\"UP\"").doesNotContain("\"success\"");
    }

    @Test
    void infoIsPublic() {
        assertThat(get(managementPort, "/actuator/info").status()).isEqualTo(200);
    }

    @Test
    void unexposedActuatorEndpointsNeverReturnTheEnvironment() {
        Reply reply = get(managementPort, "/actuator/env");
        assertThat(reply.status()).isIn(401, 403, 404);
        assertThat(String.valueOf(reply.body()))
                .doesNotContain("propertySources")
                .doesNotContain("systemProperties");
    }

    @Test
    void unauthenticatedApiCallsGetProblemDetails() {
        Reply reply = get(port, "/api/projects");
        assertThat(reply.status()).isEqualTo(401);
        assertThat(reply.body())
                .contains("\"type\":\"urn:webblock:problem:unauthenticated\"")
                .contains("\"code\":\"UNAUTHENTICATED\"")
                .contains("\"requestId\"")
                .doesNotContain("\"success\"");
    }

    @Test
    void prometheusIsScrapableWithoutATokenOnTheManagementPort() {
        Reply reply = get(managementPort, "/actuator/prometheus");
        assertThat(reply.status()).isEqualTo(200);
        assertThat(reply.body()).contains("jvm_");
    }

    @Test
    void prometheusIsNotServedOnTheApiPort() {
        assertThat(get(port, "/actuator/prometheus").status()).isIn(401, 403, 404);
    }

    /** The JDK client sends the path as written; RestClient would re-encode the percent signs. */
    private Reply rawGet(String path) throws Exception {
        java.net.http.HttpResponse<String> res = java.net.http.HttpClient.newHttpClient()
                .send(
                        java.net.http.HttpRequest.newBuilder(java.net.URI.create("http://localhost:" + port + path))
                                .GET()
                                .build(),
                        java.net.http.HttpResponse.BodyHandlers.ofString());
        return new Reply(res.statusCode(), res.body());
    }

    @Test
    void firewallRejectedPathsGetProblemDetailsWithTheOriginalPath() throws Exception {
        Reply reply = rawGet("/api/%2e%2e/x");
        assertThat(reply.status()).isEqualTo(400);
        assertThat(reply.body())
                .contains("\"type\":\"urn:webblock:problem:malformed-request\"")
                .contains("\"code\":\"MALFORMED_REQUEST\"")
                .contains("\"requestId\"")
                .contains("\"instance\":\"/api/%2e%2e/x\"");
    }

    @Test
    void semicolonPathsAreRejectedWithProblemDetailsToo() throws Exception {
        Reply reply = rawGet("/api/a;jsessionid=1/b");
        assertThat(reply.status()).isEqualTo(400);
        assertThat(reply.body()).contains("\"code\":\"MALFORMED_REQUEST\"").doesNotContain("\"instance\":\"/error\"");
    }

    @Test
    void theOutboxStoresPayloadsWithTheMessagingMapperNotTheWebMapper() {
        assertThat(payloadSerializer.getClass().getSimpleName()).isEqualTo("JsonOutboxPayloadSerializer");
    }

    // Review fix I-2 (body size).

    private HttpResponse<String> post(String path, HttpRequest.BodyPublisher body, String token) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .header("Content-Type", "application/json")
                .POST(body);
        if (token != null) {
            request.header("Authorization", "Bearer " + token);
        }
        return HttpClient.newHttpClient().send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    private static String nameOfLength(int length) {
        return "{\"name\":\"" + "a".repeat(length) + "\"}";
    }

    @Test
    void aBodyWithADeclaredLengthOverTheLimitIsRefusedBeforeAuthentication() throws Exception {
        HttpResponse<String> reply =
                post("/api/test-probe", HttpRequest.BodyPublishers.ofString(nameOfLength(5000)), null);
        assertThat(reply.statusCode()).isEqualTo(413);
        assertThat(reply.body()).contains("\"code\":\"PAYLOAD_TOO_LARGE\"").contains("\"requestId\"");
        assertThat(reply.headers().firstValue("X-Request-Id")).isPresent();
    }

    @Test
    void aChunkedBodyThatGrowsPastTheLimitIsRefused() throws Exception {
        byte[] body = nameOfLength(5000).getBytes(java.nio.charset.StandardCharsets.UTF_8);
        HttpResponse<String> reply = post(
                "/api/test-probe", HttpRequest.BodyPublishers.ofInputStream(() -> new ByteArrayInputStream(body)), "t");
        assertThat(reply.statusCode()).isEqualTo(413);
        assertThat(reply.body()).contains("\"code\":\"PAYLOAD_TOO_LARGE\"").contains("\"requestId\"");
    }

    @Test
    void aBodyUnderTheLimitIsServed() throws Exception {
        HttpResponse<String> reply =
                post("/api/test-probe", HttpRequest.BodyPublishers.ofString("{\"name\":\"ok\"}"), "t");
        assertThat(reply.statusCode()).isEqualTo(201);
        HttpResponse<String> chunked = post(
                "/api/test-probe",
                HttpRequest.BodyPublishers.ofInputStream(() -> new ByteArrayInputStream(
                        "{\"name\":\"ok\"}".getBytes(java.nio.charset.StandardCharsets.UTF_8))),
                "t");
        assertThat(chunked.statusCode()).isEqualTo(201);
    }

    // Review fix M-4 (readiness).

    @Test
    void readinessIncludesTheDatabaseAndTheBroker() {
        var readiness = healthGroups.get("readiness");
        assertThat(readiness).isNotNull();
        assertThat(readiness.isMember("readinessState")).isTrue();
        assertThat(readiness.isMember("db")).isTrue();
        assertThat(readiness.isMember("catalogDb")).isTrue();
        assertThat(readiness.isMember("rabbit")).isTrue();
        assertThat(readiness.showDetails(null)).isFalse();
        // Liveness stays independent of both.
        assertThat(healthGroups.get("liveness").isMember("db")).isFalse();
    }

    // Review fix M-5 (request ID on the container's error dispatch).

    @Test
    void containerLevelErrorsAreLoggedWithTheRequestIdOfTheResponse(CapturedOutput output) throws Exception {
        HttpResponse<String> reply = HttpClient.newHttpClient()
                .send(
                        HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/test-container-error"))
                                .GET()
                                .build(),
                        HttpResponse.BodyHandlers.ofString());
        assertThat(reply.statusCode()).isEqualTo(500);
        String id = reply.headers().firstValue("X-Request-Id").orElseThrow();
        assertThat(reply.body()).contains(id).doesNotContain("secret container failure");
        String line = output.getAll()
                .lines()
                .filter(l -> l.contains("Container-level error"))
                .findFirst()
                .orElseThrow();
        assertThat(line).contains("\"requestId\":\"" + id + "\"");
    }

    // Review fix I-1 (security): row values never reach the log or an exception message.

    @Test
    void aConstraintViolationNeverLogsTheCustomerContentOfTheRow(CapturedOutput output) {
        UUID projectId = UUID.randomUUID();
        jdbc.sql("INSERT INTO project.projects (project_id, owner_user_id, name, subdomain, business_type_id, "
                        + "component_catalog_version, status, created_at, updated_at) "
                        + "VALUES (?, ?, 'Shop', ?, ?, '1', 'draft', now(), now())")
                .params(projectId, UUID.randomUUID(), TestSubdomains.of(projectId), UUID.randomUUID())
                .update();

        Throwable failure = org.assertj.core.api.Assertions.catchThrowable(() -> jdbc.sql(
                        "INSERT INTO project.pages (page_id, project_id, name, path, puck_tree, sort_order, "
                                + "created_at, updated_at) VALUES (?, ?, 'Home', ?, ?::jsonb, 0, now(), now())")
                .params(UUID.randomUUID(), projectId, "NOT A PATH", "{\"text\":\"DISTINCTIVE-CUSTOMER-CONTENT\"}")
                .update());

        assertThat(failure).isNotNull();
        Throwable root = failure;
        while (root.getCause() != null) {
            root = root.getCause();
        }
        assertThat(root).isInstanceOf(PSQLException.class);
        PSQLException violation = (PSQLException) root;
        assertThat(violation.getServerErrorMessage().getConstraint()).isEqualTo("pages_path_check");
        assertThat(violation.getMessage() + failure.getMessage())
                .contains("pages_path_check")
                .doesNotContain("DISTINCTIVE-CUSTOMER-CONTENT")
                .doesNotContain("NOT A PATH")
                .doesNotContain("Failing row");
        assertThat(output.getAll()).doesNotContain("DISTINCTIVE-CUSTOMER-CONTENT");
    }
}
