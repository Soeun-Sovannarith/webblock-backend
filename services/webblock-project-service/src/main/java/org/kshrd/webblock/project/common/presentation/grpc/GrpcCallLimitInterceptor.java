package org.kshrd.webblock.project.common.presentation.grpc;

import io.grpc.ForwardingServerCallListener;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;
import java.util.List;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Caps the gRPC calls running at once across every service (TR-24, FR-31), so a flood cannot drain the database pool
 * that REST shares. A call takes a place only when its handler is about to run: at half-close, when grpc-java's unary
 * handler runs the service. A call that has started but not half-closed holds nothing, so a client that opens calls
 * and never finishes them blocks no one. At half-close, when no place is free, the call answers UNAVAILABLE,
 * DEPENDENCY_UNAVAILABLE and never reaches the service; else the handler runs and the place is given back once it
 * returns or throws. A cancel or complete gives nothing back: grpc-java delivers a call's steps one at a time, so the
 * place is already back by then, or was never taken. Project serves unary calls only (decision 0011); a streaming
 * method would run its service outside the half-close and is not capped here.
 */
public final class GrpcCallLimitInterceptor implements ServerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(GrpcCallLimitInterceptor.class);

    private final int maxCallsInFlight;
    private final Semaphore places;
    private final AtomicBoolean full = new AtomicBoolean();

    public GrpcCallLimitInterceptor(int maxCallsInFlight) {
        if (maxCallsInFlight < 1) {
            throw new IllegalArgumentException("maxCallsInFlight must be 1 or more");
        }
        this.maxCallsInFlight = maxCallsInFlight;
        this.places = new Semaphore(maxCallsInFlight);
    }

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
            ServerCall<ReqT, RespT> call, Metadata headers, ServerCallHandler<ReqT, RespT> next) {
        return new ForwardingServerCallListener.SimpleForwardingServerCallListener<>(next.startCall(call, headers)) {
            @Override
            public void onHalfClose() {
                if (!places.tryAcquire()) {
                    refuse(call);
                    return;
                }
                full.set(false);
                try {
                    super.onHalfClose();
                } finally {
                    places.release();
                }
            }
        };
    }

    /** Logged once each time the server becomes full, not once per refused call. */
    private <ReqT, RespT> void refuse(ServerCall<ReqT, RespT> call) {
        if (full.compareAndSet(false, true)) {
            log.warn(
                    "gRPC calls in flight reached the limit of {}; extra calls answer DEPENDENCY_UNAVAILABLE",
                    maxCallsInFlight);
        }
        GrpcErrors.close(call, ErrorCode.DEPENDENCY_UNAVAILABLE, List.of());
    }
}
