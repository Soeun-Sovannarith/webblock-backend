package org.kshrd.webblock.project.common.presentation.grpc;

import static org.assertj.core.api.Assertions.assertThat;

import com.google.protobuf.Any;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.rpc.BadRequest;
import com.google.rpc.ErrorInfo;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.protobuf.StatusProto;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.kshrd.webblock.project.common.domain.error.ErrorCategory;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;

class GrpcErrorsTest {

    /** Notion "Web Block architecture" 2.3: the status of each category. */
    private static final Map<ErrorCategory, Status.Code> BY_CATEGORY = Map.ofEntries(
            Map.entry(ErrorCategory.VALIDATION, Status.Code.INVALID_ARGUMENT),
            Map.entry(ErrorCategory.UNAUTHENTICATED, Status.Code.UNAUTHENTICATED),
            Map.entry(ErrorCategory.FORBIDDEN, Status.Code.PERMISSION_DENIED),
            Map.entry(ErrorCategory.NOT_FOUND, Status.Code.NOT_FOUND),
            Map.entry(ErrorCategory.CONFLICT, Status.Code.ABORTED),
            Map.entry(ErrorCategory.BUSINESS_RULE, Status.Code.FAILED_PRECONDITION),
            Map.entry(ErrorCategory.PAYLOAD_TOO_LARGE, Status.Code.RESOURCE_EXHAUSTED),
            Map.entry(ErrorCategory.DEPENDENCY, Status.Code.UNAVAILABLE),
            Map.entry(ErrorCategory.INTERNAL, Status.Code.INTERNAL),
            Map.entry(ErrorCategory.METHOD_NOT_ALLOWED, Status.Code.INTERNAL),
            Map.entry(ErrorCategory.NOT_ACCEPTABLE, Status.Code.INTERNAL),
            Map.entry(ErrorCategory.UNSUPPORTED_MEDIA_TYPE, Status.Code.INTERNAL));

    private static final Set<ErrorCode> ALREADY_EXISTS = Set.of(
            ErrorCode.PROJECT_ID_CONFLICT,
            ErrorCode.PAGE_PATH_TAKEN,
            ErrorCode.HOSTNAME_TAKEN,
            ErrorCode.PROJECT_NAME_TAKEN);

    private static final Set<ErrorCode> PRECONDITION_CONFLICTS =
            Set.of(ErrorCode.PROJECT_DELETED, ErrorCode.PROJECT_SUSPENDED, ErrorCode.PROJECT_NOT_DELETABLE);

    @ParameterizedTest
    @EnumSource(ErrorCode.class)
    @DisplayName("TR-25: every ErrorCode has the status of its category, with the recorded exceptions")
    void tr25_everyCodeHasItsStatus(ErrorCode code) {
        Status.Code expected;
        if (ALREADY_EXISTS.contains(code)) {
            expected = Status.Code.ALREADY_EXISTS;
        } else if (PRECONDITION_CONFLICTS.contains(code)) {
            expected = Status.Code.FAILED_PRECONDITION;
        } else {
            expected = BY_CATEGORY.get(code.category());
        }
        assertThat(GrpcErrors.statusCode(code)).as(code.code()).isEqualTo(expected);
    }

    @Test
    @DisplayName("FR-33.2: PROJECT_DELETED and PROJECT_SUSPENDED answer FAILED_PRECONDITION over gRPC")
    void fr33_2_deletedAndSuspendedAreFailedPrecondition() {
        assertThat(GrpcErrors.statusCode(ErrorCode.PROJECT_DELETED)).isEqualTo(Status.Code.FAILED_PRECONDITION);
        assertThat(GrpcErrors.statusCode(ErrorCode.PROJECT_SUSPENDED)).isEqualTo(Status.Code.FAILED_PRECONDITION);
    }

    @Test
    @DisplayName("FR-38.9: PROJECT_NOT_DELETABLE answers FAILED_PRECONDITION over gRPC")
    void fr38_9_notDeletableIsFailedPrecondition() {
        assertThat(GrpcErrors.statusCode(ErrorCode.PROJECT_NOT_DELETABLE)).isEqualTo(Status.Code.FAILED_PRECONDITION);
    }

    @Test
    @DisplayName("TR-25: an error carries the fixed sentence and one ErrorInfo with the code, domain and request ID")
    void tr25_anErrorCarriesOneErrorInfo() throws InvalidProtocolBufferException {
        StatusRuntimeException error = GrpcErrors.exception(ErrorCode.DEPENDENCY_UNAVAILABLE, List.of(), "req-1");

        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.UNAVAILABLE);
        assertThat(error.getStatus().getDescription()).isEqualTo(ErrorCode.DEPENDENCY_UNAVAILABLE.message());
        com.google.rpc.Status details = StatusProto.fromThrowable(error);
        assertThat(details).isNotNull();
        assertThat(details.getDetailsList()).hasSize(1);
        ErrorInfo info = details.getDetails(0).unpack(ErrorInfo.class);
        assertThat(info.getReason()).isEqualTo("DEPENDENCY_UNAVAILABLE");
        assertThat(info.getDomain()).isEqualTo("project.webblock");
        assertThat(info.getMetadataMap()).containsExactly(Map.entry("request_id", "req-1"));
    }

    @Test
    @DisplayName("TR-25: field problems add one BadRequest with each field, its code and its sentence")
    void tr25_fieldProblemsAddABadRequest() throws InvalidProtocolBufferException {
        StatusRuntimeException error = GrpcErrors.exception(
                ErrorCode.VALIDATION_FAILED,
                List.of(
                        ErrorDetail.of("project_id", ErrorCode.UUID_INVALID),
                        ErrorDetail.of("action", ErrorCode.FIELD_REQUIRED)),
                "req-2");

        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        com.google.rpc.Status details = StatusProto.fromThrowable(error);
        assertThat(details).isNotNull();
        List<Any> any = details.getDetailsList();
        assertThat(any).hasSize(2);
        assertThat(any.get(0).unpack(ErrorInfo.class).getReason()).isEqualTo("VALIDATION_FAILED");
        BadRequest bad = any.get(1).unpack(BadRequest.class);
        assertThat(bad.getFieldViolationsList())
                .extracting(
                        BadRequest.FieldViolation::getField,
                        BadRequest.FieldViolation::getReason,
                        BadRequest.FieldViolation::getDescription)
                .containsExactly(
                        org.assertj.core.groups.Tuple.tuple(
                                "project_id", "UUID_INVALID", ErrorCode.UUID_INVALID.message()),
                        org.assertj.core.groups.Tuple.tuple(
                                "action", "FIELD_REQUIRED", ErrorCode.FIELD_REQUIRED.message()));
    }
}
