package org.kshrd.webblock.project.support;

import org.kshrd.webblock.project.db.PostgresFixture;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.testcontainers.rabbitmq.RabbitMQContainer;

/**
 * For each application context: a fresh project database and a fresh, unmigrated catalog database (the
 * application migrates both), one shared RabbitMQ, and JWT identity (tests authenticate with MockMvc's jwt()).
 */
public final class ServiceTestDatabases implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    private static RabbitMQContainer rabbit;

    /** One RabbitMQ for every full-application test (also used by Task 7's production start-up test). */
    public static synchronized RabbitMQContainer rabbit() {
        if (rabbit == null) {
            rabbit = new RabbitMQContainer("rabbitmq:4.3.6-management-alpine");
            rabbit.start();
        }
        return rabbit;
    }

    @Override
    public void initialize(ConfigurableApplicationContext context) {
        try {
            PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
            PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
            RabbitMQContainer broker = rabbit();
            TestPropertyValues.of(
                            "spring.datasource.url=" + project.url(),
                            "spring.datasource.username=" + PostgresFixture.APP_LOGIN,
                            "spring.datasource.password=" + PostgresFixture.PASSWORD,
                            "spring.flyway.url=" + project.url(),
                            "spring.flyway.user=" + PostgresFixture.MIGRATOR_LOGIN,
                            "spring.flyway.password=" + PostgresFixture.PASSWORD,
                            "webblock.catalog.datasource.url=" + catalog.url(),
                            "webblock.catalog.datasource.username=" + PostgresFixture.CATALOG_PLATFORM_LOGIN,
                            "webblock.catalog.datasource.password=" + PostgresFixture.PASSWORD,
                            "webblock.catalog.migrations.user=" + PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                            "webblock.catalog.migrations.password=" + PostgresFixture.PASSWORD,
                            "spring.rabbitmq.host=" + broker.getHost(),
                            "spring.rabbitmq.port=" + broker.getAmqpPort(),
                            "spring.rabbitmq.username=" + broker.getAdminUsername(),
                            "spring.rabbitmq.password=" + broker.getAdminPassword(),
                            "webblock.security.identity-mode=jwt",
                            // Any free port: cached application contexts run side by side (TR-24).
                            "webblock.grpc.port=0")
                    .applyTo(context);
        } catch (Exception e) {
            throw new IllegalStateException("Test databases could not be prepared", e);
        }
    }
}
