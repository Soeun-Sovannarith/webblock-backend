package org.kshrd.webblock.project.common.presentation.grpc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.google.protobuf.InvalidProtocolBufferException;
import com.google.rpc.ErrorInfo;
import io.grpc.Attributes;
import io.grpc.Context;
import io.grpc.Metadata;
import io.grpc.MethodDescriptor;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.Status;
import io.grpc.protobuf.StatusProto;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;

/**
 * TR-24, FR-31: the call limit takes a place only when the handler is about to run (at half-close) and gives it back
 * once after it. Drives the interceptor with a recording call and handler, step by step, as grpc-java delivers them.
 */
class GrpcCallLimitInterceptorTest {

    private static final MethodDescriptor.Marshaller<String> TEXT = new MethodDescriptor.Marshaller<>() {
        @Override
        public InputStream stream(String value) {
            return new ByteArrayInputStream(value.getBytes(StandardCharsets.UTF_8));
        }

        @Override
        public String parse(InputStream stream) {
            return "";
        }
    };

    private static final MethodDescriptor<String, String> METHOD = MethodDescriptor.<String, String>newBuilder()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("test.Limit/Call")
            .setRequestMarshaller(TEXT)
            .setResponseMarshaller(TEXT)
            .build();

    private final ExecutorService threads = Executors.newVirtualThreadPerTaskExecutor();
    private final CountDownLatch release = new CountDownLatch(1);

    @AfterEach
    void close() {
        release.countDown();
        threads.shutdownNow();
    }

    @Test
    @DisplayName("TR-24, FR-31: calls that started and never half-close hold no place; a new call still runs")
    void tr24_unfinishedCallsHoldNoPlace() {
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(2);
        for (int idle = 0; idle < 8; idle++) {
            Step started = start(limit, Handler.answering());
            started.listener.onMessage("idle");
            assertThat(started.call.closedWith).isNull();
        }

        Step served = start(limit, Handler.answering());
        served.listener.onMessage("ok");
        served.listener.onHalfClose();

        assertThat(served.handler.ran).hasValue(1);
        assertThat(served.call.closedWith).isEqualTo(Status.Code.OK);
    }

    @Test
    @DisplayName("TR-24, FR-31: a half-close with every place taken answers UNAVAILABLE, DEPENDENCY_UNAVAILABLE, and "
            + "never runs the handler")
    void tr24_aFullLimitRefusesAtHalfClose() throws Exception {
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(1);
        Step held = start(limit, Handler.holding(release));
        Future<?> running = halfCloseInThread(held);
        assertThat(held.handler.entered.await(5, TimeUnit.SECONDS)).isTrue();

        Step refused = start(limit, Handler.answering());
        Context.current()
                .withValue(GrpcRequestIdInterceptor.REQUEST_ID, "full-1")
                .run(() -> {
                    refused.listener.onMessage("ok");
                    refused.listener.onHalfClose();
                });

        assertThat(refused.handler.ran).hasValue(0);
        assertThat(refused.handler.halfClosed).hasValue(0);
        assertThat(refused.call.closedWith).isEqualTo(Status.Code.UNAVAILABLE);
        assertThat(refused.call.description).isEqualTo(ErrorCode.DEPENDENCY_UNAVAILABLE.message());
        ErrorInfo info = errorInfo(refused.call);
        assertThat(info.getReason()).isEqualTo("DEPENDENCY_UNAVAILABLE");
        assertThat(info.getMetadataMap()).containsEntry("request_id", "full-1");

        release.countDown();
        running.get(5, TimeUnit.SECONDS);
        Step next = start(limit, Handler.answering());
        next.listener.onHalfClose();
        assertThat(next.handler.ran).hasValue(1);
    }

    @Test
    @DisplayName("TR-24, FR-31: a cancel or complete before half-close gives nothing back, so no place leaks")
    void tr24_aCancelBeforeHalfCloseLeaksNoPlace() throws Exception {
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(1);
        Step cancelled = start(limit, Handler.answering());
        cancelled.listener.onCancel();
        Step completed = start(limit, Handler.answering());
        completed.listener.onComplete();
        assertThat(cancelled.handler.cancelled).hasValue(1);
        assertThat(completed.handler.completed).hasValue(1);

        assertOnlyOnePlace(limit);
    }

    @Test
    @DisplayName("TR-24, FR-31: a cancel or complete after half-close gives the place back once, never twice")
    void tr24_aCancelAfterHalfCloseLeaksNoPlace() throws Exception {
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(1);
        Step served = start(limit, Handler.answering());
        served.listener.onHalfClose();
        served.listener.onCancel();
        served.listener.onComplete();
        assertThat(served.handler.ran).hasValue(1);

        assertOnlyOnePlace(limit);
    }

    @Test
    @DisplayName("TR-24, FR-31: a refused call's later complete or cancel gives nothing back")
    void tr24_aRefusedCallGivesNothingBack() throws Exception {
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(1);
        Step held = start(limit, Handler.holding(release));
        Future<?> running = halfCloseInThread(held);
        assertThat(held.handler.entered.await(5, TimeUnit.SECONDS)).isTrue();
        Step refused = start(limit, Handler.answering());
        refused.listener.onHalfClose();
        assertThat(refused.call.closedWith).isEqualTo(Status.Code.UNAVAILABLE);
        refused.listener.onComplete();
        refused.listener.onCancel();
        release.countDown();
        running.get(5, TimeUnit.SECONDS);

        assertOnlyOnePlace(limit);
    }

    @Test
    @DisplayName("TR-24, FR-31: a handler that throws still gives its place back")
    void tr24_aThrowingHandlerGivesItsPlaceBack() throws Exception {
        GrpcCallLimitInterceptor limit = new GrpcCallLimitInterceptor(1);
        Step failing = start(limit, Handler.throwing());

        assertThatThrownBy(failing.listener::onHalfClose).isInstanceOf(IllegalStateException.class);

        assertOnlyOnePlace(limit);
    }

    @Test
    @DisplayName("TR-24: the limit must be 1 or more")
    void tr24_theLimitIsAtLeastOne() {
        assertThatThrownBy(() -> new GrpcCallLimitInterceptor(0)).isInstanceOf(IllegalArgumentException.class);
    }

    /** Proves the limit has exactly one free place: one held call runs, a second is refused, then one runs again. */
    private void assertOnlyOnePlace(GrpcCallLimitInterceptor limit) throws Exception {
        CountDownLatch hold = new CountDownLatch(1);
        try {
            Step held = start(limit, Handler.holding(hold));
            Future<?> running = halfCloseInThread(held);
            assertThat(held.handler.entered.await(5, TimeUnit.SECONDS)).isTrue();

            Step second = start(limit, Handler.answering());
            second.listener.onHalfClose();
            assertThat(second.handler.ran).as("a second call while one is held").hasValue(0);
            assertThat(second.call.closedWith).isEqualTo(Status.Code.UNAVAILABLE);

            hold.countDown();
            running.get(5, TimeUnit.SECONDS);
        } finally {
            hold.countDown();
        }
        Step after = start(limit, Handler.answering());
        after.listener.onHalfClose();
        assertThat(after.handler.ran).as("a call once the held one ended").hasValue(1);
    }

    private Future<?> halfCloseInThread(Step step) {
        return threads.submit(step.listener::onHalfClose);
    }

    private static Step start(GrpcCallLimitInterceptor limit, Handler handler) {
        RecordingCall call = new RecordingCall();
        ServerCall.Listener<String> listener = limit.interceptCall(call, new Metadata(), handler);
        return new Step(call, handler, listener);
    }

    private static ErrorInfo errorInfo(RecordingCall call) throws InvalidProtocolBufferException {
        com.google.rpc.Status details = StatusProto.fromStatusAndTrailers(call.status, call.trailers);
        assertThat(details).isNotNull();
        return details.getDetails(0).unpack(ErrorInfo.class);
    }

    private record Step(RecordingCall call, Handler handler, ServerCall.Listener<String> listener) {}

    /** A unary handler: its work runs at half-close, as grpc-java's does, and answers OK on its call. */
    private static final class Handler implements ServerCallHandler<String, String> {

        final AtomicInteger halfClosed = new AtomicInteger();
        final AtomicInteger ran = new AtomicInteger();
        final AtomicInteger cancelled = new AtomicInteger();
        final AtomicInteger completed = new AtomicInteger();
        final CountDownLatch entered = new CountDownLatch(1);
        private final CountDownLatch hold;
        private final boolean fail;

        private Handler(CountDownLatch hold, boolean fail) {
            this.hold = hold;
            this.fail = fail;
        }

        static Handler answering() {
            return new Handler(null, false);
        }

        static Handler holding(CountDownLatch hold) {
            return new Handler(hold, false);
        }

        static Handler throwing() {
            return new Handler(null, true);
        }

        @Override
        public ServerCall.Listener<String> startCall(ServerCall<String, String> call, Metadata headers) {
            return new ServerCall.Listener<>() {
                @Override
                public void onHalfClose() {
                    halfClosed.incrementAndGet();
                    entered.countDown();
                    if (fail) {
                        throw new IllegalStateException("handler failed");
                    }
                    if (hold != null) {
                        try {
                            assertThat(hold.await(10, TimeUnit.SECONDS)).isTrue();
                        } catch (InterruptedException interrupted) {
                            Thread.currentThread().interrupt();
                            throw new IllegalStateException("interrupted", interrupted);
                        }
                    }
                    ran.incrementAndGet();
                    call.close(Status.OK, new Metadata());
                }

                @Override
                public void onCancel() {
                    cancelled.incrementAndGet();
                }

                @Override
                public void onComplete() {
                    completed.incrementAndGet();
                }
            };
        }
    }

    /** Records how the call was closed. */
    private static final class RecordingCall extends ServerCall<String, String> {

        volatile Status.Code closedWith;
        volatile String description;
        volatile Status status;
        volatile Metadata trailers;

        @Override
        public void request(int numMessages) {}

        @Override
        public void sendHeaders(Metadata headers) {}

        @Override
        public void sendMessage(String message) {}

        @Override
        public void close(Status status, Metadata trailers) {
            assertThat(closedWith).as("closed once").isNull();
            this.status = status;
            this.trailers = trailers;
            this.description = status.getDescription();
            this.closedWith = status.getCode();
        }

        @Override
        public boolean isCancelled() {
            return false;
        }

        @Override
        public Attributes getAttributes() {
            return Attributes.EMPTY;
        }

        @Override
        public MethodDescriptor<String, String> getMethodDescriptor() {
            return METHOD;
        }
    }
}
