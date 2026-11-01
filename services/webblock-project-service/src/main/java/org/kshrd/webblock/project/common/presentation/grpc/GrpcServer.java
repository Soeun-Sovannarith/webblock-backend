package org.kshrd.webblock.project.common.presentation.grpc;

import io.grpc.BindableService;
import io.grpc.Server;
import io.grpc.ServerInterceptors;
import io.grpc.ServerServiceDefinition;
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.kshrd.webblock.project.common.application.config.GrpcProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;

/**
 * The one gRPC server of the process (TR-24, decision 0011): plain grpc-java on Netty, on {@code webblock.grpc.port},
 * serving every {@link BindableService} bean through the request-ID, call-limit and error interceptors, on virtual
 * threads. Each bean must sit in a {@code presentation.grpc} package of this service, or the server refuses to start.
 * An inbound message is at most 64 KiB and its metadata at most 16 KiB. At most {@code webblock.grpc.max-calls-in-flight}
 * calls run at once. Netty bounds each connection: concurrent calls, idle time, age, keepalive pings and stream
 * resets. A method it does not serve answers UNIMPLEMENTED without echoing the path. There is no reflection or health
 * service. The port reads no token: STRICT mTLS and the mesh AuthorizationPolicy are its gate. At stop, calls in
 * flight may finish within {@code webblock.grpc.shutdown-grace}; then they are cut.
 */
@Component
@EnableConfigurationProperties(GrpcProperties.class)
public class GrpcServer implements SmartLifecycle {

    static final int MAX_INBOUND_MESSAGE_BYTES = 64 * 1024;
    static final int MAX_INBOUND_METADATA_BYTES = 16 * 1024;

    /** Per connection (HTTP/2 SETTINGS_MAX_CONCURRENT_STREAMS); the call limit caps the whole server. */
    static final int MAX_CONCURRENT_CALLS_PER_CONNECTION = 100;

    static final Duration MAX_CONNECTION_IDLE = Duration.ofMinutes(5);
    static final Duration MAX_CONNECTION_AGE = Duration.ofMinutes(30);
    static final Duration MAX_CONNECTION_AGE_GRACE = Duration.ofSeconds(10);

    /** A client may ping at most this often, and only while it has calls open; more often closes the connection. */
    static final Duration PERMIT_KEEP_ALIVE_TIME = Duration.ofMinutes(1);

    /** HTTP/2 rapid reset (CVE-2023-44487): more stream resets than this in one window close the connection. */
    static final int MAX_RST_FRAMES = 200;

    static final int RST_WINDOW_SECONDS = 10;

    /**
     * Where a service bean must sit: a {@code presentation.grpc} package of this service, that is one feature folder of
     * {@code presentation.grpc} or common's {@code presentation.grpc} (decision 0016).
     */
    private static final String BASE_PACKAGE = "org.kshrd.webblock.project.";

    private static final String FEATURE_SERVICE_PACKAGES = BASE_PACKAGE + "presentation.grpc.";

    private static final String COMMON_SERVICE_PACKAGE = BASE_PACKAGE + "common.presentation.grpc";

    private static final Logger log = LoggerFactory.getLogger(GrpcServer.class);

    /** After the grace, how long the cut calls get to end before the stop returns anyway. */
    private static final long FORCED_STOP_MILLIS = 2_000;

    private final List<BindableService> services;
    private final GrpcProperties properties;

    private Server server;
    private ExecutorService executor;

    public GrpcServer(List<BindableService> services, GrpcProperties properties) {
        services.forEach(GrpcServer::requireServicePackage);
        this.services = List.copyOf(services);
        this.properties = properties;
    }

    /**
     * Refuses a service bean outside a {@code presentation.grpc} package of this service, so a library that declares
     * one (health, reflection, channelz, admin) is never served on the tokenless port unseen.
     */
    private static void requireServicePackage(BindableService service) {
        String type = service.getClass().getName();
        String pkg = service.getClass().getPackageName();
        boolean inFeatureFolder = pkg.startsWith(FEATURE_SERVICE_PACKAGES)
                && pkg.length() > FEATURE_SERVICE_PACKAGES.length()
                && pkg.indexOf('.', FEATURE_SERVICE_PACKAGES.length()) < 0;
        if (!inFeatureFolder && !pkg.equals(COMMON_SERVICE_PACKAGE)) {
            throw new IllegalStateException("gRPC service " + type + " is not in a presentation.grpc package of "
                    + BASE_PACKAGE + "; the gRPC server serves only this service's own gRPC services");
        }
    }

    /**
     * A service with the server's interceptors: the request ID runs first, so an error answer carries it; then the
     * call limit, shared by every service of one server; then the error interceptor. Tests serve a service
     * in-process through this same chain.
     */
    public static ServerServiceDefinition intercepted(BindableService service, GrpcCallLimitInterceptor limit) {
        return ServerInterceptors.intercept(service, new GrpcErrorInterceptor(), limit, new GrpcRequestIdInterceptor());
    }

    @Override
    public synchronized void start() {
        if (server != null) {
            return;
        }
        executor = Executors.newVirtualThreadPerTaskExecutor();
        NettyServerBuilder builder = NettyServerBuilder.forPort(properties.port())
                .executor(executor)
                .maxInboundMessageSize(MAX_INBOUND_MESSAGE_BYTES)
                .maxInboundMetadataSize(MAX_INBOUND_METADATA_BYTES)
                .maxConcurrentCallsPerConnection(MAX_CONCURRENT_CALLS_PER_CONNECTION)
                .maxConnectionIdle(MAX_CONNECTION_IDLE.toMillis(), TimeUnit.MILLISECONDS)
                .maxConnectionAge(MAX_CONNECTION_AGE.toMillis(), TimeUnit.MILLISECONDS)
                .maxConnectionAgeGrace(MAX_CONNECTION_AGE_GRACE.toMillis(), TimeUnit.MILLISECONDS)
                .permitKeepAliveTime(PERMIT_KEEP_ALIVE_TIME.toMillis(), TimeUnit.MILLISECONDS)
                .permitKeepAliveWithoutCalls(false)
                .maxRstFramesPerWindow(MAX_RST_FRAMES, RST_WINDOW_SECONDS)
                .fallbackHandlerRegistry(new GrpcUnknownMethodRegistry());
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(properties.maxCallsInFlight());
        services.forEach(service -> builder.addService(intercepted(service, limit)));
        try {
            server = builder.build().start();
        } catch (IOException e) {
            executor.shutdownNow();
            executor = null;
            throw new UncheckedIOException("The gRPC server could not start on port " + properties.port(), e);
        }
        log.info(
                "gRPC server listening on port {}, serving {}, at most {} calls at once",
                server.getPort(),
                serviceNames(),
                properties.maxCallsInFlight());
    }

    @Override
    public synchronized void stop() {
        if (server == null) {
            return;
        }
        server.shutdown();
        try {
            if (!server.awaitTermination(properties.shutdownGrace().toMillis(), TimeUnit.MILLISECONDS)) {
                log.warn("gRPC calls still running after the shutdown grace are cut");
                server.shutdownNow();
                server.awaitTermination(FORCED_STOP_MILLIS, TimeUnit.MILLISECONDS);
            }
        } catch (InterruptedException interrupted) {
            server.shutdownNow();
            Thread.currentThread().interrupt();
        } finally {
            executor.shutdownNow();
            server = null;
            executor = null;
        }
    }

    @Override
    public synchronized boolean isRunning() {
        return server != null;
    }

    /** The full names of the services the running server serves, in the order they were added. */
    public synchronized List<String> serviceNames() {
        if (server == null) {
            throw new IllegalStateException("The gRPC server is not running");
        }
        return server.getServices().stream()
                .map(service -> service.getServiceDescriptor().getName())
                .toList();
    }

    /** The port the server listens on (the bound one when the setting is 0). */
    public synchronized int port() {
        if (server == null) {
            throw new IllegalStateException("The gRPC server is not running");
        }
        return server.getPort();
    }
}
