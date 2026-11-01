package org.kshrd.webblock.project.common.application.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.time.Duration;
import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.ConfigurationPropertySources;
import org.springframework.boot.context.properties.source.MapConfigurationPropertySource;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.io.ClassPathResource;

class GrpcPropertiesTest {

    private static GrpcProperties bind(Map<String, String> values) {
        return new Binder(new MapConfigurationPropertySource(values))
                .bindOrCreate("webblock.grpc", GrpcProperties.class);
    }

    @Test
    @DisplayName("TR-24: with no settings the port is 9090, the grace 10 seconds and 8 calls may run at once")
    void tr24_defaults() {
        GrpcProperties properties = bind(Map.of());
        assertThat(properties.port()).isEqualTo(9090);
        assertThat(properties.shutdownGrace()).isEqualTo(Duration.ofSeconds(10));
        assertThat(properties.maxCallsInFlight()).isEqualTo(8);
    }

    @Test
    @DisplayName("TR-24: application.yml sets port 9090, a 10 second grace and 8 calls in flight, below the pool")
    void tr24_applicationYmlSetsThePortAndTheGrace() throws IOException {
        var sources = new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"));
        GrpcProperties properties = new Binder(ConfigurationPropertySources.from(sources))
                .bindOrCreate("webblock.grpc", GrpcProperties.class);
        assertThat(properties.port()).isEqualTo(9090);
        assertThat(properties.shutdownGrace()).isEqualTo(Duration.ofSeconds(10));
        assertThat(properties.maxCallsInFlight()).isEqualTo(8);
        // Hikari's default pool is 10 connections, shared with REST: gRPC alone can never take all of them.
        Integer pool = new Binder(ConfigurationPropertySources.from(sources))
                .bind("spring.datasource.hikari.maximum-pool-size", Integer.class)
                .orElse(10);
        assertThat(properties.maxCallsInFlight()).isLessThan(pool);
    }

    @Test
    @DisplayName("TR-24: a port outside 0 to 65535, a negative grace or fewer than 1 call in flight stops the start-up")
    void tr24_badValuesAreRefused() {
        assertThatThrownBy(() -> new GrpcProperties(-1, Duration.ofSeconds(1), 8))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new GrpcProperties(65_536, Duration.ofSeconds(1), 8))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new GrpcProperties(9090, Duration.ofSeconds(-1), 8))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new GrpcProperties(9090, null, 8)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new GrpcProperties(9090, Duration.ofSeconds(1), 0))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(new GrpcProperties(0, Duration.ZERO, 1).port()).isZero();
    }
}
