package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import java.time.Duration;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalManagementPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.client.RestClient;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.rabbitmq.RabbitMQContainer;

/** Review fix I3: the outbox gauges are served on /actuator/prometheus and move while a record is stuck. */
@Tag("messaging")
@ActiveProfiles("prototype")
@Testcontainers
@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {
            "management.server.port=0",
            "webblock.grpc.port=0",
            "namastack.outbox.polling.fixed.interval=200ms",
            "webblock.catalog.migrations.enabled=false",
            "webblock.catalog.datasource.url=jdbc:postgresql://127.0.0.1:1/unused"
        })
@Import(OutboxSpikeBeans.class)
class OutboxMetricsTest {

    static final String AGE = "webblock_outbox_oldest_unsent_age_seconds";
    static final String MAX_FAILURES = "webblock_outbox_max_failures";
    static final String FAILED = "webblock_outbox_failed_records";

    @Container
    @ServiceConnection
    static final RabbitMQContainer rabbit = new RabbitMQContainer("rabbitmq:4.3.6-management-alpine");

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
    }

    @LocalManagementPort
    int managementPort;

    @Autowired
    ProbeService probes;

    @Test
    void theGaugesAppearAndMoveWhileARecordIsStuck() {
        assertThat(gauge(AGE)).isZero();
        assertThat(gauge(MAX_FAILURES)).isZero();
        assertThat(gauge(FAILED)).isZero();

        // A local job that fails three times (2 s fixed backoff) before it succeeds: stuck for about 6 s.
        probes.schedule(new ProbeJob(UUID.randomUUID(), 3));

        await().atMost(Duration.ofSeconds(15))
                .pollInterval(Duration.ofMillis(250))
                .untilAsserted(() -> assertThat(gauge(MAX_FAILURES)).isGreaterThanOrEqualTo(2));
        assertThat(gauge(AGE)).isGreaterThan(1.0);

        await().atMost(Duration.ofSeconds(20))
                .pollInterval(Duration.ofMillis(250))
                .untilAsserted(() -> {
                    assertThat(gauge(AGE)).isZero();
                    assertThat(gauge(MAX_FAILURES)).isZero();
                });
        assertThat(gauge(FAILED)).isZero();
    }

    private double gauge(String name) {
        String body = RestClient.create("http://localhost:" + managementPort)
                .get()
                .uri("/actuator/prometheus")
                .retrieve()
                .body(String.class);
        Matcher m = Pattern.compile("(?m)^" + name + "(?:\\{[^}]*\\})? (\\S+)$").matcher(String.valueOf(body));
        assertThat(m.find()).as("%s on /actuator/prometheus", name).isTrue();
        return Double.parseDouble(m.group(1));
    }
}
