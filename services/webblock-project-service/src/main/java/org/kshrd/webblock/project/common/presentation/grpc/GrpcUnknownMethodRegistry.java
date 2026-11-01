package org.kshrd.webblock.project.common.presentation.grpc;

import io.grpc.HandlerRegistry;
import io.grpc.MethodDescriptor;
import io.grpc.ServerCall;
import io.grpc.ServerInterceptors;
import io.grpc.ServerMethodDefinition;
import io.grpc.ServerServiceDefinition;
import io.grpc.Status;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.Objects;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;

/**
 * The server's answer to a method it does not serve (TR-25, decision 0011): UNIMPLEMENTED with the fixed sentence of
 * {@code ROUTE_NOT_FOUND}, one ErrorInfo and the {@code x-request-id}. grpc-java's own answer would echo the path the
 * caller sent; this one never does. The handler reads no message and reaches no service.
 */
final class GrpcUnknownMethodRegistry extends HandlerRegistry {

    private static final MethodDescriptor.Marshaller<byte[]> DISCARD = new MethodDescriptor.Marshaller<>() {
        @Override
        public InputStream stream(byte[] value) {
            return new ByteArrayInputStream(value);
        }

        @Override
        public byte[] parse(InputStream stream) {
            try (stream) {
                return new byte[0];
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    };

    private static final MethodDescriptor<byte[], byte[]> UNKNOWN = MethodDescriptor.<byte[], byte[]>newBuilder()
            .setType(MethodDescriptor.MethodType.UNKNOWN)
            .setFullMethodName("project.unknown/Method")
            .setRequestMarshaller(DISCARD)
            .setResponseMarshaller(DISCARD)
            .build();

    private final ServerMethodDefinition<?, ?> answer;

    GrpcUnknownMethodRegistry() {
        ServerServiceDefinition unknown = ServerServiceDefinition.builder(UNKNOWN.getServiceName())
                .addMethod(UNKNOWN, (call, headers) -> {
                    refuse(call);
                    return new ServerCall.Listener<>() {};
                })
                .build();
        answer = Objects.requireNonNull(ServerInterceptors.intercept(unknown, new GrpcRequestIdInterceptor())
                .getMethod(UNKNOWN.getFullMethodName()));
    }

    @Override
    public ServerMethodDefinition<?, ?> lookupMethod(String methodName, String authority) {
        return answer;
    }

    private static void refuse(ServerCall<byte[], byte[]> call) {
        GrpcErrors.close(call, Status.Code.UNIMPLEMENTED, ErrorCode.ROUTE_NOT_FOUND, List.of());
    }
}
