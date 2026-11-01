package org.kshrd.webblock.project.common.presentation.grpc;

import io.grpc.Context;
import io.grpc.Contexts;
import io.grpc.ForwardingServerCall;
import io.grpc.ForwardingServerCallListener;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;
import io.grpc.Status;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.slf4j.MDC;

/**
 * Gives every gRPC call an ID, as {@code RequestIdFilter} does for REST (TR-25): the caller's {@code x-request-id} when
 * {@link RequestIds#acceptedOrNew} keeps it, else a new one. The ID is in the call's {@link Context}, in the logging
 * context during each step of the call, and in the answer's headers (or trailers, for an error with no headers).
 */
public final class GrpcRequestIdInterceptor implements ServerInterceptor {

    /** The call's request ID, for the error answer's ErrorInfo. */
    static final Context.Key<String> REQUEST_ID = Context.key(RequestIds.MDC_KEY);

    static final Metadata.Key<String> HEADER = Metadata.Key.of(RequestIds.HEADER, Metadata.ASCII_STRING_MARSHALLER);

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
            ServerCall<ReqT, RespT> call, Metadata headers, ServerCallHandler<ReqT, RespT> next) {
        String id = RequestIds.acceptedOrNew(headers.get(HEADER));
        ServerCall<ReqT, RespT> tagged = new TaggedCall<>(call, id);
        Context context = Context.current().withValue(REQUEST_ID, id);
        MDC.put(RequestIds.MDC_KEY, id);
        try {
            return new LoggedListener<>(Contexts.interceptCall(context, tagged, headers, next), id);
        } finally {
            MDC.remove(RequestIds.MDC_KEY);
        }
    }

    /** Sends the ID back once: in the headers, or in the trailers of an answer that sends none. */
    private static final class TaggedCall<ReqT, RespT>
            extends ForwardingServerCall.SimpleForwardingServerCall<ReqT, RespT> {

        private final String id;
        private boolean headersSent;

        TaggedCall(ServerCall<ReqT, RespT> call, String id) {
            super(call);
            this.id = id;
        }

        @Override
        public void sendHeaders(Metadata headers) {
            headers.put(HEADER, id);
            headersSent = true;
            super.sendHeaders(headers);
        }

        @Override
        public void close(Status status, Metadata trailers) {
            if (!headersSent) {
                trailers.put(HEADER, id);
            }
            super.close(status, trailers);
        }
    }

    /** Each step of a call may run on another thread: the logging context is set around every one. */
    private static final class LoggedListener<ReqT>
            extends ForwardingServerCallListener.SimpleForwardingServerCallListener<ReqT> {

        private final String id;

        LoggedListener(ServerCall.Listener<ReqT> listener, String id) {
            super(listener);
            this.id = id;
        }

        private void logged(Runnable step) {
            MDC.put(RequestIds.MDC_KEY, id);
            try {
                step.run();
            } finally {
                MDC.remove(RequestIds.MDC_KEY);
            }
        }

        @Override
        public void onMessage(ReqT message) {
            logged(() -> super.onMessage(message));
        }

        @Override
        public void onHalfClose() {
            logged(() -> super.onHalfClose());
        }

        @Override
        public void onCancel() {
            logged(() -> super.onCancel());
        }

        @Override
        public void onComplete() {
            logged(() -> super.onComplete());
        }

        @Override
        public void onReady() {
            logged(() -> super.onReady());
        }
    }
}
