package org.kshrd.webblock.project.infrastructure.persistence.migration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowable;

import java.sql.Connection;
import java.sql.ResultSet;
import java.util.LinkedHashMap;
import java.util.Map;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.WebBlockApplication;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServedPaths;
import org.kshrd.webblock.project.support.ServiceTestDatabases;
import org.springdoc.webmvc.api.OpenApiWebMvcResource;
import org.springdoc.webmvc.ui.SwaggerWelcomeWebMvc;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;
import org.testcontainers.rabbitmq.RabbitMQContainer;

/** Decision 0005: in production the service never migrates and never holds a migrator credential. */
@Tag("integration")
class ProductionStartupTest {

    /** Command-line arguments, so they win over application.yml and its profile files (J0's spike does the same). */
    private static ConfigurableApplicationContext start(String projectUrl, String catalogUrl, String... overrides) {
        RabbitMQContainer rabbit = ServiceTestDatabases.rabbit();
        Map<String, String> settings = new LinkedHashMap<>();
        settings.put("spring.datasource.url", projectUrl);
        settings.put("spring.datasource.username", PostgresFixture.APP_LOGIN);
        settings.put("spring.datasource.password", PostgresFixture.PASSWORD);
        settings.put("spring.flyway.password", "");
        settings.put("webblock.catalog.migrations.password", "");
        settings.put("webblock.catalog.datasource.url", catalogUrl);
        settings.put("webblock.catalog.datasource.username", PostgresFixture.CATALOG_PLATFORM_LOGIN);
        settings.put("webblock.catalog.datasource.password", PostgresFixture.PASSWORD);
        settings.put("spring.rabbitmq.host", rabbit.getHost());
        settings.put("spring.rabbitmq.port", String.valueOf(rabbit.getAmqpPort()));
        settings.put("spring.rabbitmq.username", rabbit.getAdminUsername());
        settings.put("spring.rabbitmq.password", rabbit.getAdminPassword());
        settings.put(
                "spring.security.oauth2.resourceserver.jwt.issuer-uri", "https://issuer.example.test/realms/webblock");
        // A deployment replaces application.yml's placeholder hosts; ProfileGuard refuses them in production.
        settings.put("webblock.payment.base-url", "https://payment.webblock.example");
        settings.put("webblock.platform.site-domain", "sites.webblock.example");
        settings.put("server.port", "0");
        settings.put("management.server.port", "0");
        settings.put("webblock.grpc.port", "0");
        for (String override : overrides) {
            int eq = override.indexOf('=');
            settings.put(override.substring(0, eq), override.substring(eq + 1));
        }
        return new SpringApplicationBuilder(WebBlockApplication.class)
                .web(WebApplicationType.SERVLET)
                .profiles("production")
                .run(settings.entrySet().stream()
                        .map(e -> "--" + e.getKey() + "=" + e.getValue())
                        .toArray(String[]::new));
    }

    private static long count(Connection c, String sql) throws Exception {
        try (ResultSet rs = c.createStatement().executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    private static final String OUR_TABLES = "SELECT count(*) FROM pg_tables WHERE schemaname IN "
            + "('project', 'project_messaging', 'project_ops', 'catalog', 'catalog_flyway')";

    @Test
    void theProductionProfileNeverMigrates() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        Throwable failure =
                catchThrowable(() -> start(project.url(), catalog.url()).close());
        // The empty schema is refused by the first bean that needs it (Hibernate's schema validation of the JPA
        // entities, or the outbox's relation, SQL state 42P01), not by the profile guard and not by a migration.
        assertThat(failure)
                .isNotNull()
                .satisfiesAnyOf(
                        f -> assertThat(f).hasStackTraceContaining("Schema validation: missing table"),
                        f -> assertThat(f).hasStackTraceContaining("does not exist"));
        assertThat(failure).isNotInstanceOf(IllegalStateException.class);
        try (Connection p = project.asSuperuser();
                Connection c = catalog.asSuperuser()) {
            assertThat(count(p, OUR_TABLES)).isZero();
            assertThat(count(c, OUR_TABLES)).isZero();
        }
    }

    @Test
    void theProductionProfileStartsAgainstDatabasesTheJobMigrated() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        assertThat(Migrations.run(MigrationsTest.settings(project.url(), catalog.url())))
                .isZero();
        long projectHistory;
        long catalogHistory;
        try (Connection p = project.asSuperuser();
                Connection c = catalog.asSuperuser()) {
            projectHistory = count(p, "SELECT count(*) FROM project_ops.flyway_schema_history");
            catalogHistory = count(c, "SELECT count(*) FROM catalog_flyway.flyway_schema_history");
        }
        try (ConfigurableApplicationContext context = start(project.url(), catalog.url())) {
            assertThat(context.isActive()).isTrue();
            assertThat(context.getBeanProvider(Flyway.class).getIfAvailable()).isNull();
        }
        try (Connection p = project.asSuperuser();
                Connection c = catalog.asSuperuser()) {
            assertThat(count(p, "SELECT count(*) FROM project_ops.flyway_schema_history"))
                    .isEqualTo(projectHistory);
            assertThat(count(c, "SELECT count(*) FROM catalog_flyway.flyway_schema_history"))
                    .isEqualTo(catalogHistory);
        }
    }

    @Test
    @DisplayName("TR-40: production with application.yml's settings serves no OpenAPI description and no Swagger UI")
    void tr40_theProductionProfileServesNoOpenApiDescription() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        assertThat(Migrations.run(MigrationsTest.settings(project.url(), catalog.url())))
                .isZero();
        try (ConfigurableApplicationContext context = start(project.url(), catalog.url())) {
            assertThat(context.getBeanNamesForType(OpenApiWebMvcResource.class)).isEmpty();
            assertThat(context.getBeanNamesForType(SwaggerWelcomeWebMvc.class)).isEmpty();
            assertThat(ServedPaths.of(context))
                    .contains("/api/v1/projects")
                    .noneMatch(path -> path.contains("api-docs") || path.contains("swagger-ui"));
        }
    }

    @Test
    void theProductionProfileRefusesAMigratorCredential() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        assertThatThrownBy(() -> start(project.url(), catalog.url(), "spring.flyway.password=secret"))
                .hasMessage("The production service must not hold migrator credentials");
    }

    @Test
    void anOverriddenProjectFlywayIsRefusedBeforeItMigrates() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        assertThatThrownBy(() -> start(
                        project.url(),
                        catalog.url(),
                        "spring.flyway.enabled=true",
                        "spring.flyway.user=" + PostgresFixture.MIGRATOR_LOGIN,
                        "spring.flyway.password=" + PostgresFixture.PASSWORD))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no migrations in-process");
        assertNothingMigrated(project, catalog);
    }

    @Test
    void anOverriddenCatalogRunnerIsRefusedBeforeItMigrates() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        assertThatThrownBy(() -> start(
                        project.url(),
                        catalog.url(),
                        "webblock.catalog.migrations.enabled=true",
                        "webblock.catalog.migrations.user=" + PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                        "webblock.catalog.migrations.password=" + PostgresFixture.PASSWORD))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no migrations in-process");
        assertNothingMigrated(project, catalog);
    }

    private static void assertNothingMigrated(PostgresFixture.Database project, PostgresFixture.CatalogDatabase catalog)
            throws Exception {
        try (Connection p = project.asSuperuser();
                Connection c = catalog.asSuperuser()) {
            assertThat(count(p, OUR_TABLES)).isZero();
            assertThat(count(c, OUR_TABLES)).isZero();
        }
    }
}
