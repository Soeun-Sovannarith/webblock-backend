package org.kshrd.webblock.project.common.infrastructure.config;

import static org.assertj.core.api.Assertions.assertThat;

import com.zaxxer.hikari.HikariDataSource;
import java.io.IOException;
import java.time.Duration;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Bindable;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.ConfigurationPropertySources;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.io.ClassPathResource;

/**
 * application.yml bounds the broker connect and keeps the project pool's connections alive, bound the way Spring Boot
 * binds spring.datasource.hikari onto the HikariDataSource bean.
 */
class ConnectionResilienceSettingsTest {

    private static Binder binder;

    @BeforeAll
    static void loadApplicationYml() throws IOException {
        var sources = new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"));
        binder = new Binder(ConfigurationPropertySources.from(sources));
    }

    @Test
    @DisplayName("TR-35: an unreachable RabbitMQ fails the connect after 3 s instead of the client's 60 s default")
    void tr35_theRabbitConnectIsBoundedAtThreeSeconds() {
        assertThat(binder.bind("spring.rabbitmq.connection-timeout", Duration.class)
                        .get())
                .isEqualTo(Duration.ofSeconds(3));
    }

    @Test
    @DisplayName("TR-35: the project pool keeps idle connections alive, recycles them and validates within 3 s")
    void tr35_theProjectPoolKeepsConnectionsAliveAndRecyclesThem() {
        try (HikariDataSource pool = binder.bind(
                        "spring.datasource.hikari", Bindable.ofInstance(new HikariDataSource()))
                .get()) {
            assertThat(pool.getKeepaliveTime()).isEqualTo(Duration.ofSeconds(60).toMillis());
            assertThat(pool.getMaxLifetime()).isEqualTo(Duration.ofMinutes(10).toMillis());
            assertThat(pool.getIdleTimeout()).isEqualTo(Duration.ofMinutes(5).toMillis());
            assertThat(pool.getMinimumIdle()).isEqualTo(2);
            assertThat(pool.getValidationTimeout())
                    .isEqualTo(Duration.ofSeconds(3).toMillis());
            // Hikari's 30 s default stays; the validation timeout must stay below it
            assertThat(pool.getConnectionTimeout())
                    .isEqualTo(Duration.ofSeconds(30).toMillis());
            // the existing bounds are untouched
            assertThat(pool.getConnectionInitSql()).isEqualTo("SET statement_timeout = '10s'; SET lock_timeout = '5s'");
            assertThat(pool.getDataSourceProperties())
                    .containsEntry("logServerErrorDetail", "false")
                    .containsEntry("socketTimeout", "15");
        }
    }
}
