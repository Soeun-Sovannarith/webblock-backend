package org.kshrd.webblock.project.common.presentation.grpc;

import io.grpc.ForwardingServerCall;
import io.grpc.ForwardingServerCallListener;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;
import io.grpc.Status;
import java.util.List;
import org.kshrd.webblock.project.common.domain.error.ErrorCategory;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.presentation.error.DatabaseBusy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Turns a failure of a gRPC service into a closed call through {@link GrpcErrors} (TR-25), as
 * {@code GlobalExceptionHandler} does for REST: a {@link WebBlockException} answers its code, a busy database (lock
 * wait, statement timeout, no free pooled connection: {@link DatabaseBusy}) answers DEPENDENCY_UNAVAILABLE with a
 * warning and no stack trace, and anything else is logged without request content and answers
 * INTERNAL_ERROR.
 */
public final class GrpcErrorInterceptor implements ServerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(GrpcErrorInterceptor.class);

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
            ServerCall<ReqT, RespT> call, Metadata headers, ServerCallHandler<ReqT, RespT> next) {
        GuardedCall<ReqT, RespT> guarded = new GuardedCall<>(call);
        ServerCall.Listener<ReqT> listener;
        try {
            listener = next.startCall(guarded, headers);
        } catch (RuntimeException failure) {
            guarded.fail(failure);
            return new ServerCall.Listener<>() {};
        }
        return new ForwardingServerCallListener.SimpleForwardingServerCallListener<>(listener) {
            @Override
            public void onMessage(ReqT message) {
                try {
                    super.onMessage(message);
                } catch (RuntimeException failure) {
                    guarded.fail(failure);
                }
            }

            @Override
            public void onHalfClose() {
                try {
                    super.onHalfClose();
                } catch (RuntimeException failure) {
                    guarded.fail(failure);
                }
            }

            @Override
            public void onReady() {
                try {
                    super.onReady();
                } catch (RuntimeException failure) {
                    guarded.fail(failure);
                }
            }
        };
    }

    /** The code and field problems a failure answers with, logged as REST logs it. */
    static Answer answer(RuntimeException failure, String method) {
        ErrorCode code;
        List<ErrorDetail> details = List.of();
        if (failure instanceof WebBlockException refusal) {
            code = refusal.errorCode();
            details = refusal.details();
            if (code.category() == ErrorCategory.INTERNAL) {
                log.error("gRPC call {} failed with {}", method, code.code(), failure);
            }
        } else if (DatabaseBusy.is(failure)) {
            log.warn(
                    "Database busy in gRPC call {}: {}",
                    method,
                    failure.getClass().getSimpleName());
            code = ErrorCode.DEPENDENCY_UNAVAILABLE;
        } else {
            log.error("Unhandled exception in gRPC call {}", method, failure);
            code = ErrorCode.INTERNAL_ERROR;
        }
        return new Answer(code, details);
    }

    /** What a failed call answers: a code and its field problems. */
    record Answer(ErrorCode code, List<ErrorDetail> details) {}

    /** Remembers whether the call was closed, so a failure after the answer never closes it twice. */
    private static final class GuardedCall<ReqT, RespT>
            extends ForwardingServerCall.SimpleForwardingServerCall<ReqT, RespT> {

        private volatile boolean closed;

        GuardedCall(ServerCall<ReqT, RespT> call) {
            super(call);
        }

        @Override
        public void close(Status status, Metadata trailers) {
            closed = true;
            super.close(status, trailers);
        }

        void fail(RuntimeException failure) {
            String method = getMethodDescriptor().getFullMethodName();
            Answer answer = answer(failure, method);
            if (closed) {
                log.warn("gRPC call {} failed after it answered", method);
                return;
            }
            GrpcErrors.close(this, answer.code(), answer.details());
        }
    }
}
