package org.kshrd.webblock.project.common.application.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * The gRPC server (TR-24, decision 0011): {@code webblock.grpc.port} (0 to 65535; 0 takes any free port, for tests),
 * {@code webblock.grpc.shutdown-grace}, how long calls in flight may finish at stop before they are cut, and
 * {@code webblock.grpc.max-calls-in-flight}, how many calls may run at once (at least 1). It stays below the project
 * database's pool (10 connections), which REST shares, so a flood of gRPC calls cannot take every connection.
 */
@ConfigurationProperties("webblock.grpc")
public record GrpcProperties(
        @DefaultValue("9090") int port,
        @DefaultValue("10s") Duration shutdownGrace,
        @DefaultValue("8") int maxCallsInFlight) {

    public GrpcProperties {
        if (port < 0 || port > 65_535) {
            throw new IllegalArgumentException("webblock.grpc.port must be 0 to 65535");
        }
        if (shutdownGrace == null || shutdownGrace.isNegative()) {
            throw new IllegalArgumentException("webblock.grpc.shutdown-grace must be zero or more");
        }
        if (maxCallsInFlight < 1) {
            throw new IllegalArgumentException("webblock.grpc.max-calls-in-flight must be 1 or more");
        }
    }
}
