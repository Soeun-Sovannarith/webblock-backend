package org.kshrd.webblock.project.common.presentation.grpc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowableOfType;
import static org.awaitility.Awaitility.await;
import static org.mockito.Mockito.mock;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.rpc.ErrorInfo;
import io.grpc.BindableService;
import io.grpc.CallOptions;
import io.grpc.ClientCall;
import io.grpc.Grpc;
import io.grpc.InsecureChannelCredentials;
import io.grpc.ManagedChannel;
import io.grpc.Metadata;
import io.grpc.MethodDescriptor;
import io.grpc.ServerServiceDefinition;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.protobuf.StatusProto;
import io.grpc.stub.ClientCalls;
import io.grpc.stub.MetadataUtils;
import io.grpc.stub.ServerCalls;
import io.grpc.stub.StreamObserver;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.sql.SQLTransientConnectionException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.access.presentation.grpc.FeatureFirstGrpcService;
import org.kshrd.webblock.project.common.application.config.GrpcProperties;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.kshrd.webblock.project.presentation.grpc.LayerRootGrpcService;
import org.kshrd.webblock.project.presentation.grpc.layoutfixture.FeatureFolderGrpcService;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.transaction.CannotCreateTransactionException;
import webblock.project.v1.Access.CheckAccessRequest;
import webblock.project.v1.Access.CheckAccessResponse;
import webblock.project.v1.AccessServiceGrpc;

/** TR-24 and TR-25: the one gRPC server, its limits, its interceptors and its stop, on Netty and a free port. */
class GrpcServerTest {

    private static final Metadata.Key<String> REQUEST_ID =
            Metadata.Key.of(RequestIds.HEADER, Metadata.ASCII_STRING_MARSHALLER);
    private static final String UUID_SHAPE = "[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}";

    private final FakeAccessService access = new FakeAccessService();
    private GrpcServer server;
    private ManagedChannel channel;

    @AfterEach
    void close() throws InterruptedException {
        access.release.countDown();
        if (channel != null) {
            channel.shutdownNow();
            channel.awaitTermination(5, TimeUnit.SECONDS);
        }
        if (server != null) {
            server.stop();
        }
    }

    private GrpcServer start(Duration grace) {
        return start(grace, 8);
    }

    private GrpcServer start(Duration grace, int maxCallsInFlight) {
        server = new GrpcServer(
                List.of(access, GrpcServerTest::echoService), new GrpcProperties(0, grace, maxCallsInFlight));
        server.start();
        channel = Grpc.newChannelBuilderForAddress("localhost", server.port(), InsecureChannelCredentials.create())
                .build();
        return server;
    }

    private AccessServiceGrpc.AccessServiceBlockingStub stub() {
        return AccessServiceGrpc.newBlockingStub(channel).withDeadlineAfter(10, TimeUnit.SECONDS);
    }

    private static CheckAccessRequest request(String userId) {
        return CheckAccessRequest.newBuilder()
                .setUserId(userId)
                .setProjectId("p")
                .setAction("a")
                .build();
    }

    @Test
    @DisplayName("TR-24: the server starts on its port and serves every service bean")
    void tr24_startsOnThePortAndServesEveryServiceBean() {
        start(Duration.ofSeconds(1));

        assertThat(server.isRunning()).isTrue();
        assertThat(server.port()).isPositive();
        assertThat(stub().checkAccess(request("ok")).getAllowed()).isTrue();
        assertThat(ClientCalls.blockingUnaryCall(channel, ECHO, io.grpc.CallOptions.DEFAULT, "hello"))
                .isEqualTo("hello");
    }

    @Test
    @DisplayName("TR-24: start and stop happen once; the port is unknown while stopped")
    void tr24_startAndStopAreIdempotent() {
        server = new GrpcServer(List.of(access), new GrpcProperties(0, Duration.ofSeconds(1), 8));
        assertThat(server.isRunning()).isFalse();
        assertThatThrownBy(server::port).isInstanceOf(IllegalStateException.class);
        server.stop();

        server.start();
        int port = server.port();
        server.start();
        assertThat(server.port()).isEqualTo(port);

        server.stop();
        server.stop();
        assertThat(server.isRunning()).isFalse();
    }

    @Test
    @DisplayName("TR-24: a port already in use stops the start-up")
    void tr24_aTakenPortStopsTheStartUp() {
        start(Duration.ofSeconds(1));
        GrpcServer second =
                new GrpcServer(List.of(access), new GrpcProperties(server.port(), Duration.ofSeconds(1), 8));

        assertThatThrownBy(second::start)
                .isInstanceOf(UncheckedIOException.class)
                .hasMessageContaining(Integer.toString(server.port()));
        assertThat(second.isRunning()).isFalse();
    }

    @Test
    @DisplayName("TR-24: a 65 KiB request answers RESOURCE_EXHAUSTED and never reaches the service")
    void tr24_aRequestOver64KiBIsRefused() {
        start(Duration.ofSeconds(1));

        StatusRuntimeException refused = catchThrowableOfType(
                StatusRuntimeException.class, () -> stub().checkAccess(request("x".repeat(65 * 1024))));

        assertThat(refused.getStatus().getCode()).isEqualTo(Status.Code.RESOURCE_EXHAUSTED);
        assertThat(access.calls).hasValue(0);
    }

    @Test
    @DisplayName("TR-24: a request just under 64 KiB is served")
    void tr24_aRequestUnder64KiBIsServed() {
        start(Duration.ofSeconds(1));

        assertThat(stub().checkAccess(request("ok" + "x".repeat(63 * 1024))).getAllowed())
                .isTrue();
    }

    @Test
    @DisplayName("TR-24: metadata over 16 KiB is refused and never reaches the service")
    void tr24_metadataOver16KiBIsRefused() {
        start(Duration.ofSeconds(1));
        Metadata big = new Metadata();
        big.put(Metadata.Key.of("x-filler", Metadata.ASCII_STRING_MARSHALLER), "x".repeat(17 * 1024));

        StatusRuntimeException refused = catchThrowableOfType(
                StatusRuntimeException.class,
                () -> stub().withInterceptors(MetadataUtils.newAttachHeadersInterceptor(big))
                        .checkAccess(request("ok")));

        // Netty refuses the header block at the HTTP/2 layer (the client reads INTERNAL); the call never starts.
        assertThat(refused.getStatus().getCode()).isNotEqualTo(Status.Code.OK);
        assertThat(access.calls).hasValue(0);
    }

    @Test
    @DisplayName("TR-24: no reflection and no health service")
    void tr24_noReflectionOrHealthService() {
        start(Duration.ofSeconds(1));

        for (String method : List.of(
                "grpc.reflection.v1.ServerReflection/ServerReflectionInfo",
                "grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo",
                "grpc.health.v1.Health/Check")) {
            StatusRuntimeException refused = catchThrowableOfType(
                    StatusRuntimeException.class,
                    () -> ClientCalls.blockingUnaryCall(channel, unary(method), io.grpc.CallOptions.DEFAULT, ""));
            assertThat(refused.getStatus().getCode()).as(method).isEqualTo(Status.Code.UNIMPLEMENTED);
        }
    }

    @Test
    @DisplayName("TR-24: a call in flight finishes within the grace, and the stop returns once it has")
    void tr24_aCallInFlightFinishesWithinTheGrace() throws Exception {
        start(Duration.ofSeconds(5));
        ListenableFuture<CheckAccessResponse> answer =
                AccessServiceGrpc.newFutureStub(channel).checkAccess(request("slow"));
        assertThat(access.entered.await(5, TimeUnit.SECONDS)).isTrue();

        long started = System.nanoTime();
        server.stop();
        Duration stopping = Duration.ofNanos(System.nanoTime() - started);

        assertThat(answer.get(5, TimeUnit.SECONDS).getAllowed()).isTrue();
        assertThat(stopping).isLessThan(Duration.ofSeconds(4));
        assertThat(server.isRunning()).isFalse();
    }

    @Test
    @DisplayName("TR-24: a call still running at the end of the grace is cut, and the stop returns")
    void tr24_aStuckCallIsCutAfterTheGrace() throws Exception {
        start(Duration.ofMillis(300));
        ListenableFuture<CheckAccessResponse> answer =
                AccessServiceGrpc.newFutureStub(channel).checkAccess(request("stuck"));
        assertThat(access.entered.await(5, TimeUnit.SECONDS)).isTrue();

        long started = System.nanoTime();
        server.stop();
        Duration stopping = Duration.ofNanos(System.nanoTime() - started);

        assertThat(stopping).isBetween(Duration.ofMillis(250), Duration.ofSeconds(3));
        assertThat(server.isRunning()).isFalse();
        ExecutionException cut = catchThrowableOfType(ExecutionException.class, () -> answer.get(5, TimeUnit.SECONDS));
        assertThat(Status.fromThrowable(cut).getCode()).isNotEqualTo(Status.Code.OK);
    }

    @Test
    @DisplayName("TR-25: a safe x-request-id is kept: in the logging context and in the answer's headers")
    void tr25_aSafeRequestIdIsKept() {
        start(Duration.ofSeconds(1));
        AtomicReference<Metadata> headers = new AtomicReference<>();
        AtomicReference<Metadata> trailers = new AtomicReference<>();
        Metadata sent = new Metadata();
        sent.put(REQUEST_ID, "trace-me.1_2");

        CheckAccessResponse answer = stub().withInterceptors(
                        MetadataUtils.newAttachHeadersInterceptor(sent),
                        MetadataUtils.newCaptureMetadataInterceptor(headers, trailers))
                .checkAccess(request("mdc"));

        assertThat(answer.getReason()).isEqualTo("trace-me.1_2");
        assertThat(headers.get().get(REQUEST_ID)).isEqualTo("trace-me.1_2");
    }

    @Test
    @DisplayName("TR-25: an unsafe x-request-id is replaced; the error's ErrorInfo and trailer carry the new one")
    void tr25_anUnsafeRequestIdIsReplaced() throws InvalidProtocolBufferException {
        start(Duration.ofSeconds(1));
        AtomicReference<Metadata> headers = new AtomicReference<>();
        AtomicReference<Metadata> trailers = new AtomicReference<>();
        Metadata sent = new Metadata();
        sent.put(REQUEST_ID, "not safe!");

        StatusRuntimeException error = catchThrowableOfType(
                StatusRuntimeException.class,
                () -> stub().withInterceptors(
                                MetadataUtils.newAttachHeadersInterceptor(sent),
                                MetadataUtils.newCaptureMetadataInterceptor(headers, trailers))
                        .checkAccess(request("not-found")));

        String id = trailers.get().get(REQUEST_ID);
        assertThat(id).matches(UUID_SHAPE);
        assertThat(errorInfo(error).getMetadataMap()).containsEntry("request_id", id);
    }

    @Test
    @DisplayName("TR-25: a request with no x-request-id gets one")
    void tr25_aMissingRequestIdIsMade() {
        start(Duration.ofSeconds(1));

        assertThat(stub().checkAccess(request("mdc")).getReason()).matches(UUID_SHAPE);
    }

    @Test
    @DisplayName("TR-25: a WebBlockException answers its code's status, ErrorInfo and BadRequest")
    void tr25_aRefusalAnswersItsCode() throws InvalidProtocolBufferException {
        start(Duration.ofSeconds(1));

        StatusRuntimeException notFound =
                catchThrowableOfType(StatusRuntimeException.class, () -> stub().checkAccess(request("not-found")));
        StatusRuntimeException invalid =
                catchThrowableOfType(StatusRuntimeException.class, () -> stub().checkAccess(request("invalid")));

        assertThat(notFound.getStatus().getCode()).isEqualTo(Status.Code.NOT_FOUND);
        assertThat(errorInfo(notFound).getReason()).isEqualTo("PROJECT_NOT_FOUND");
        assertThat(invalid.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(errorInfo(invalid).getReason()).isEqualTo("VALIDATION_FAILED");
        assertThat(StatusProto.fromThrowable(invalid).getDetailsList()).hasSize(2);
    }

    @Test
    @DisplayName("TR-25: a lock wait answers UNAVAILABLE, DEPENDENCY_UNAVAILABLE, and never the database's message")
    void tr25_aLockWaitIsDependencyUnavailable() throws InvalidProtocolBufferException {
        start(Duration.ofSeconds(1));

        StatusRuntimeException busy =
                catchThrowableOfType(StatusRuntimeException.class, () -> stub().checkAccess(request("busy")));

        assertThat(busy.getStatus().getCode()).isEqualTo(Status.Code.UNAVAILABLE);
        assertThat(busy.getStatus().getDescription()).isEqualTo(ErrorCode.DEPENDENCY_UNAVAILABLE.message());
        assertThat(errorInfo(busy).getReason()).isEqualTo("DEPENDENCY_UNAVAILABLE");
    }

    @Test
    @DisplayName("TR-25: an unknown failure answers INTERNAL, INTERNAL_ERROR, and never its message")
    void tr25_anUnknownFailureIsInternal() throws InvalidProtocolBufferException {
        start(Duration.ofSeconds(1));

        StatusRuntimeException failed =
                catchThrowableOfType(StatusRuntimeException.class, () -> stub().checkAccess(request("secret")));

        assertThat(failed.getStatus().getCode()).isEqualTo(Status.Code.INTERNAL);
        assertThat(failed.getStatus().getDescription()).isEqualTo(ErrorCode.INTERNAL_ERROR.message());
        assertThat(errorInfo(failed).getReason()).isEqualTo("INTERNAL_ERROR");
        assertThat(failed.toString()).doesNotContain("secret detail");
    }

    @Test
    @DisplayName("TR-25: a failure after the answer was sent leaves the answer as it was")
    void tr25_aFailureAfterTheAnswerKeepsTheAnswer() {
        start(Duration.ofSeconds(1));

        assertThat(stub().checkAccess(request("late-failure")).getAllowed()).isTrue();
    }

    @Test
    @DisplayName("TR-24: the server serves exactly the services it is given, and names them")
    void tr24_theServerNamesTheServicesItServes() {
        start(Duration.ofSeconds(1));

        assertThat(server.serviceNames()).containsExactly("webblock.project.v1.AccessService", "test.Echo");
    }

    @Test
    @DisplayName("TR-24: a service bean outside a presentation.grpc package stops the start-up")
    void tr24_aServiceOutsidePresentationGrpcStopsTheStartUp() {
        BindableService stray = mock(BindableService.class);
        GrpcProperties properties = new GrpcProperties(0, Duration.ofSeconds(1), 8);

        assertThatThrownBy(() -> new GrpcServer(List.of(access, stray), properties))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(stray.getClass().getName())
                .hasMessageContaining("presentation.grpc");
    }

    @Test
    @DisplayName("TR-24, TR-5: a service bean in a feature folder of presentation.grpc is served")
    void tr24_aServiceInAFeatureFolderOfPresentationGrpcIsServed() {
        server = new GrpcServer(
                List.of(new FeatureFolderGrpcService(), access), new GrpcProperties(0, Duration.ofSeconds(1), 8));
        server.start();

        assertThat(server.isRunning()).isTrue();
    }

    @Test
    @DisplayName("TR-24, TR-5: a service bean with no feature folder, or in the old feature-first folder, stops the "
            + "start-up")
    void tr24_aServiceOutsideAFeatureFolderStopsTheStartUp() {
        GrpcProperties properties = new GrpcProperties(0, Duration.ofSeconds(1), 8);

        for (BindableService stray :
                List.<BindableService>of(new LayerRootGrpcService(), new FeatureFirstGrpcService())) {
            assertThatThrownBy(() -> new GrpcServer(List.of(access, stray), properties))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining(stray.getClass().getName())
                    .hasMessageContaining("presentation.grpc");
        }
    }

    @Test
    @DisplayName("TR-24, FR-31: calls beyond the cap answer UNAVAILABLE, DEPENDENCY_UNAVAILABLE, and never reach the "
            + "service")
    void tr24_aFloodBeyondTheCapIsUnavailable() throws Exception {
        start(Duration.ofSeconds(1), 2);
        List<ListenableFuture<CheckAccessResponse>> held = List.of(
                AccessServiceGrpc.newFutureStub(channel).checkAccess(request("hold")),
                AccessServiceGrpc.newFutureStub(channel).checkAccess(request("hold")));
        assertThat(access.held.await(5, TimeUnit.SECONDS)).isTrue();
        AtomicReference<Metadata> headers = new AtomicReference<>();
        AtomicReference<Metadata> trailers = new AtomicReference<>();

        for (int extra = 0; extra < 5; extra++) {
            StatusRuntimeException full = catchThrowableOfType(
                    StatusRuntimeException.class,
                    () -> stub().withInterceptors(MetadataUtils.newCaptureMetadataInterceptor(headers, trailers))
                            .checkAccess(request("ok")));

            assertThat(full.getStatus().getCode()).isEqualTo(Status.Code.UNAVAILABLE);
            assertThat(full.getStatus().getDescription()).isEqualTo(ErrorCode.DEPENDENCY_UNAVAILABLE.message());
            assertThat(errorInfo(full).getReason()).isEqualTo("DEPENDENCY_UNAVAILABLE");
            assertThat(errorInfo(full).getMetadataMap())
                    .containsEntry("request_id", trailers.get().get(REQUEST_ID));
        }
        assertThat(access.calls).hasValue(2);

        access.release.countDown();
        for (ListenableFuture<CheckAccessResponse> answer : held) {
            assertThat(answer.get(5, TimeUnit.SECONDS).getAllowed()).isTrue();
        }
        await().atMost(Duration.ofSeconds(5))
                .untilAsserted(
                        () -> assertThat(stub().checkAccess(request("ok")).getAllowed())
                                .isTrue());
    }

    @Test
    @DisplayName("TR-24, FR-31: calls a client opens and never half-closes hold no place; other calls are answered")
    void tr24_unfinishedCallsDoNotBlockOtherCalls() {
        start(Duration.ofSeconds(1), 2);
        List<ClientCall<CheckAccessRequest, CheckAccessResponse>> idle = new ArrayList<>();
        for (int open = 0; open < 8; open++) {
            ClientCall<CheckAccessRequest, CheckAccessResponse> call =
                    channel.newCall(AccessServiceGrpc.getCheckAccessMethod(), CallOptions.DEFAULT);
            call.start(new ClientCall.Listener<>() {}, new Metadata());
            call.request(1);
            call.sendMessage(request("ok"));
            idle.add(call);
        }
        try {
            for (int served = 0; served < 5; served++) {
                assertThat(stub().checkAccess(request("ok")).getAllowed()).isTrue();
            }
            assertThat(access.calls).hasValue(5);
        } finally {
            idle.forEach(call -> call.cancel("test ends", null));
        }
    }

    @Test
    @DisplayName("TR-24: a call the caller cancels gives its place back once its work has ended")
    void tr24_aCancelledCallGivesItsPlaceBack() throws Exception {
        start(Duration.ofSeconds(1), 1);
        ListenableFuture<CheckAccessResponse> held =
                AccessServiceGrpc.newFutureStub(channel).checkAccess(request("hold"));
        await().atMost(Duration.ofSeconds(5))
                .untilAsserted(() -> assertThat(access.held.getCount()).isOne());

        held.cancel(true);
        StatusRuntimeException stillBusy =
                catchThrowableOfType(StatusRuntimeException.class, () -> stub().checkAccess(request("ok")));
        assertThat(stillBusy.getStatus().getCode()).isEqualTo(Status.Code.UNAVAILABLE);

        access.release.countDown();
        await().atMost(Duration.ofSeconds(5))
                .untilAsserted(
                        () -> assertThat(stub().checkAccess(request("ok")).getAllowed())
                                .isTrue());
    }

    @Test
    @DisplayName("TR-25: an unknown method answers UNIMPLEMENTED with ErrorInfo and x-request-id, never its path")
    void tr25_anUnknownMethodIsUnimplementedAndNotEchoed() throws InvalidProtocolBufferException {
        start(Duration.ofSeconds(1));
        AtomicReference<Metadata> headers = new AtomicReference<>();
        AtomicReference<Metadata> trailers = new AtomicReference<>();
        Metadata sent = new Metadata();
        sent.put(REQUEST_ID, "unknown-1");

        StatusRuntimeException refused = catchThrowableOfType(
                StatusRuntimeException.class,
                () -> ClientCalls.blockingUnaryCall(
                        channel, unary("evil.Probe/SecretPathMarker"), io.grpc.CallOptions.DEFAULT, ""));
        StatusRuntimeException tagged = catchThrowableOfType(
                StatusRuntimeException.class,
                () -> ClientCalls.blockingUnaryCall(
                        io.grpc.ClientInterceptors.intercept(
                                channel,
                                MetadataUtils.newAttachHeadersInterceptor(sent),
                                MetadataUtils.newCaptureMetadataInterceptor(headers, trailers)),
                        unary("evil.Probe/SecretPathMarker"),
                        io.grpc.CallOptions.DEFAULT,
                        ""));

        assertThat(refused.getStatus().getCode()).isEqualTo(Status.Code.UNIMPLEMENTED);
        assertThat(refused.getStatus().getDescription()).isEqualTo(ErrorCode.ROUTE_NOT_FOUND.message());
        assertThat(refused.toString()).doesNotContain("SecretPathMarker").doesNotContain("evil");
        assertThat(errorInfo(refused).getReason()).isEqualTo("ROUTE_NOT_FOUND");
        assertThat(errorInfo(refused).getMetadataMap().get("request_id")).matches(UUID_SHAPE);
        assertThat(trailers.get().get(REQUEST_ID)).isEqualTo("unknown-1");
        assertThat(errorInfo(tagged).getMetadataMap()).containsEntry("request_id", "unknown-1");
    }

    @Test
    @DisplayName(
            "TR-24, FR-31: no free pooled connection answers UNAVAILABLE, DEPENDENCY_UNAVAILABLE, with no ERROR log")
    void tr24_aPoolTimeoutIsDependencyUnavailable() throws InvalidProtocolBufferException {
        start(Duration.ofSeconds(1));
        Logger logger = (Logger) LoggerFactory.getLogger(GrpcErrorInterceptor.class);
        ListAppender<ILoggingEvent> logs = new ListAppender<>();
        logs.start();
        logger.addAppender(logs);
        try {
            StatusRuntimeException busy =
                    catchThrowableOfType(StatusRuntimeException.class, () -> stub().checkAccess(request("pool")));

            assertThat(busy.getStatus().getCode()).isEqualTo(Status.Code.UNAVAILABLE);
            assertThat(errorInfo(busy).getReason()).isEqualTo("DEPENDENCY_UNAVAILABLE");
            assertThat(busy.toString()).doesNotContain("HikariPool");
            assertThat(logs.list).isNotEmpty().allSatisfy(event -> {
                assertThat(event.getLevel()).isEqualTo(Level.WARN);
                assertThat(event.getThrowableProxy()).isNull();
            });
        } finally {
            logger.detachAppender(logs);
        }
    }

    private static ErrorInfo errorInfo(StatusRuntimeException error) throws InvalidProtocolBufferException {
        com.google.rpc.Status details = StatusProto.fromThrowable(error);
        assertThat(details).isNotNull();
        return details.getDetails(0).unpack(ErrorInfo.class);
    }

    /** Answers by the user ID it is sent: each one drives one path of the server. */
    static final class FakeAccessService extends AccessServiceGrpc.AccessServiceImplBase {

        final AtomicInteger calls = new AtomicInteger();
        final CountDownLatch entered = new CountDownLatch(1);
        final CountDownLatch held = new CountDownLatch(2);
        final CountDownLatch release = new CountDownLatch(1);

        @Override
        public void checkAccess(CheckAccessRequest request, StreamObserver<CheckAccessResponse> answer) {
            calls.incrementAndGet();
            String reason = "";
            switch (request.getUserId()) {
                case "not-found" -> throw new WebBlockException(ErrorCode.PROJECT_NOT_FOUND);
                case "invalid" ->
                    throw new WebBlockException(
                            ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("action", ErrorCode.VALUE_INVALID)));
                case "busy" -> throw new CannotAcquireLockException("lock wait on project.projects");
                case "pool" ->
                    throw new CannotCreateTransactionException(
                            "Could not open JPA EntityManager for transaction",
                            new SQLTransientConnectionException(
                                    "HikariPool-1 - Connection is not available, request timed out after 30000ms."));
                case "secret" -> throw new IllegalStateException("secret detail");
                case "mdc" -> reason = MDC.get(RequestIds.MDC_KEY);
                case "slow" -> {
                    entered.countDown();
                    pause(Duration.ofMillis(300));
                }
                case "hold" -> {
                    held.countDown();
                    awaitRelease();
                }
                case "stuck" -> {
                    entered.countDown();
                    pause(Duration.ofSeconds(30));
                }
                default -> {}
            }
            answer.onNext(CheckAccessResponse.newBuilder()
                    .setAllowed(true)
                    .setReason(reason)
                    .build());
            answer.onCompleted();
            if (request.getUserId().equals("late-failure")) {
                throw new IllegalStateException("after the answer");
            }
        }

        private void awaitRelease() {
            try {
                assertThat(release.await(30, TimeUnit.SECONDS)).isTrue();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("interrupted", interrupted);
            }
        }

        private void pause(Duration duration) {
            try {
                assertThat(release.await(duration.toMillis(), TimeUnit.MILLISECONDS))
                        .isFalse();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("interrupted", interrupted);
            }
        }
    }

    private static final MethodDescriptor.Marshaller<String> TEXT = new MethodDescriptor.Marshaller<>() {
        @Override
        public InputStream stream(String value) {
            return new ByteArrayInputStream(value.getBytes(StandardCharsets.UTF_8));
        }

        @Override
        public String parse(InputStream stream) {
            try {
                return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    };

    private static MethodDescriptor<String, String> unary(String fullName) {
        return MethodDescriptor.<String, String>newBuilder()
                .setType(MethodDescriptor.MethodType.UNARY)
                .setFullMethodName(fullName)
                .setRequestMarshaller(TEXT)
                .setResponseMarshaller(TEXT)
                .build();
    }

    private static final MethodDescriptor<String, String> ECHO = unary("test.Echo/Echo");

    /** A second service bean: the server registers every one it is given. */
    private static ServerServiceDefinition echoService() {
        return ServerServiceDefinition.builder("test.Echo")
                .addMethod(ECHO, ServerCalls.asyncUnaryCall((String text, StreamObserver<String> answer) -> {
                    answer.onNext(text);
                    answer.onCompleted();
                }))
                .build();
    }
}
