package org.kshrd.webblock.project.common.presentation.grpc;

import com.google.protobuf.Any;
import com.google.rpc.BadRequest;
import com.google.rpc.ErrorInfo;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.protobuf.StatusProto;
import java.util.List;
import java.util.Objects;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.support.RequestIds;

/**
 * Every {@link ErrorCode} as a gRPC error (TR-25, Notion "Web Block architecture" 2.3 and 3.4): the status of its
 * category, the code's fixed sentence as the description, one {@code ErrorInfo} and, for field problems, one
 * {@code BadRequest}. Like a Problem Details body, an error never echoes caller input.
 */
public final class GrpcErrors {

    /** The {@code ErrorInfo} domain of every error Project answers. */
    public static final String DOMAIN = "project.webblock";

    private GrpcErrors() {}

    /**
     * The status of a code. A switch over every constant, with no default: a new code without a status does not
     * compile (design G16).
     */
    public static Status.Code statusCode(ErrorCode code) {
        return switch (code) {
            case VALIDATION_FAILED,
                    MALFORMED_REQUEST,
                    FIELD_REQUIRED,
                    FIELD_LENGTH_INVALID,
                    FIELD_OUT_OF_RANGE,
                    VALUE_INVALID,
                    UUID_INVALID,
                    PAGE_PATH_INVALID,
                    IDEMPOTENCY_KEY_INVALID,
                    PUCK_TREE_INVALID -> Status.Code.INVALID_ARGUMENT;
            case UNAUTHENTICATED -> Status.Code.UNAUTHENTICATED;
            case ACCESS_DENIED -> Status.Code.PERMISSION_DENIED;
            case ROUTE_NOT_FOUND,
                    PROJECT_NOT_FOUND,
                    PAGE_NOT_FOUND,
                    COMPONENT_CATALOG_NOT_FOUND,
                    BUSINESS_TYPE_NOT_FOUND,
                    TEMPLATE_NOT_FOUND,
                    VERSION_NOT_FOUND,
                    GENERATION_CONTRACT_NOT_FOUND,
                    GENERATION_REQUEST_NOT_FOUND -> Status.Code.NOT_FOUND;
            case CONCURRENT_UPDATE,
                    ADMISSION_RELEASE_PENDING,
                    STALE_EDIT_REVISION,
                    IDEMPOTENCY_KEY_REUSED,
                    GENERATION_IN_PROGRESS,
                    REQUEST_NOT_GENERATING -> Status.Code.ABORTED;
            case PROJECT_ID_CONFLICT, PAGE_PATH_TAKEN, HOSTNAME_TAKEN, PROJECT_NAME_TAKEN -> Status.Code.ALREADY_EXISTS;
            // Conflicts a caller cannot retry its way out of (web-block-commons PLAN.md Q2).
            case PROJECT_DELETED, PROJECT_SUSPENDED, PROJECT_NOT_DELETABLE -> Status.Code.FAILED_PRECONDITION;
            case PROJECT_READ_ONLY,
                    PAGE_LIMIT_REACHED,
                    BUSINESS_TYPE_NOT_AVAILABLE,
                    TEMPLATE_NOT_AVAILABLE,
                    PRODUCT_TYPE_NOT_ALLOWED,
                    WEBSITE_LIMIT_REACHED,
                    TRIAL_ENDED,
                    NO_ACTIVE_PLAN,
                    CLIENT_SUSPENDED,
                    WEBSITE_NOT_ADMITTED -> Status.Code.FAILED_PRECONDITION;
            case PAYLOAD_TOO_LARGE -> Status.Code.RESOURCE_EXHAUSTED;
            case DEPENDENCY_UNAVAILABLE, CATALOG_UNAVAILABLE, PAYMENT_UNAVAILABLE -> Status.Code.UNAVAILABLE;
            // INTERNAL, and the HTTP-only categories (405, 406, 415), which a gRPC call cannot meet.
            case INTERNAL_ERROR, METHOD_NOT_ALLOWED, NOT_ACCEPTABLE, UNSUPPORTED_MEDIA_TYPE -> Status.Code.INTERNAL;
        };
    }

    /**
     * Closes a call with the error of a code, carrying the call's request ID: the one place the server answers a call
     * with an {@link ErrorCode} (the call limit, the error interceptor and the unknown-method answer use it).
     */
    static void close(ServerCall<?, ?> call, ErrorCode code, List<ErrorDetail> details) {
        close(call, statusCode(code), code, details);
    }

    /** The same close with another status: only the unknown-method answer uses it (UNIMPLEMENTED, decision 0011). */
    static void close(ServerCall<?, ?> call, Status.Code statusCode, ErrorCode code, List<ErrorDetail> details) {
        String requestId = Objects.requireNonNullElseGet(
                GrpcRequestIdInterceptor.REQUEST_ID.get(), () -> RequestIds.acceptedOrNew(null));
        StatusRuntimeException error = exception(statusCode, code, details, requestId);
        call.close(error.getStatus(), Objects.requireNonNullElseGet(error.getTrailers(), Metadata::new));
    }

    /** The error a call answers with: status, description, ErrorInfo and, when there are field problems, BadRequest. */
    public static StatusRuntimeException exception(ErrorCode code, List<ErrorDetail> details, String requestId) {
        return exception(statusCode(code), code, details, requestId);
    }

    /** The same error with another status, for {@link #close(ServerCall, Status.Code, ErrorCode, List)}. */
    private static StatusRuntimeException exception(
            Status.Code statusCode, ErrorCode code, List<ErrorDetail> details, String requestId) {
        com.google.rpc.Status.Builder status = com.google.rpc.Status.newBuilder()
                .setCode(statusCode.value())
                .setMessage(code.message())
                .addDetails(Any.pack(ErrorInfo.newBuilder()
                        .setReason(code.code())
                        .setDomain(DOMAIN)
                        .putMetadata("request_id", requestId)
                        .build()));
        if (!details.isEmpty()) {
            BadRequest.Builder badRequest = BadRequest.newBuilder();
            for (ErrorDetail detail : details) {
                badRequest.addFieldViolations(BadRequest.FieldViolation.newBuilder()
                        .setField(Objects.requireNonNullElse(detail.field(), ""))
                        .setReason(detail.code())
                        .setDescription(detail.message()));
            }
            status.addDetails(Any.pack(badRequest.build()));
        }
        return StatusProto.toStatusRuntimeException(status.build());
    }
}
