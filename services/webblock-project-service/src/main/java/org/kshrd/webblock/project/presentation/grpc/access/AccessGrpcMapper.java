package org.kshrd.webblock.project.presentation.grpc.access;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import org.kshrd.webblock.project.application.access.model.AccessDecision;
import org.kshrd.webblock.project.application.access.request.AccessCheckRequest;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.presentation.error.FieldErrors;
import org.springframework.stereotype.Component;
import webblock.project.v1.Access.CheckAccessRequest;
import webblock.project.v1.Access.CheckAccessResponse;

/**
 * CheckAccess to and from the access feature (TR-26): the proto request becomes the REST request record and is
 * checked with its Bean Validation rules; field problems name the proto fields ({@code user_id}, {@code project_id},
 * {@code action}). Proto3 has no "missing", so an empty string reads as missing.
 */
@Component
class AccessGrpcMapper {

    private static final List<String> FIELD_ORDER = List.of("user_id", "project_id", "action");

    private final Validator validator;

    AccessGrpcMapper(Validator validator) {
        this.validator = validator;
    }

    /** The checked request; any problem is VALIDATION_FAILED with one field error per problem. */
    AccessCheckRequest toRequest(CheckAccessRequest proto) {
        List<ErrorDetail> problems = new ArrayList<>();
        AccessCheckRequest request = new AccessCheckRequest(
                emptyAsMissing(proto.getUserId()),
                emptyAsMissing(proto.getProjectId()),
                emptyAsMissing(proto.getAction()));
        for (ConstraintViolation<AccessCheckRequest> violation : validator.validate(request)) {
            ErrorDetail problem = FieldErrors.of(violation);
            problems.add(new ErrorDetail(snakeCase(problem.field()), problem.code(), problem.message()));
        }
        if (!problems.isEmpty()) {
            problems.sort(Comparator.comparingInt((ErrorDetail problem) -> FIELD_ORDER.indexOf(problem.field()))
                    .thenComparing(ErrorDetail::code));
            throw new WebBlockException(ErrorCode.VALIDATION_FAILED, problems);
        }
        return request;
    }

    /** The answer: {@code reason} is empty when allowed. */
    CheckAccessResponse toResponse(AccessDecision decision) {
        return CheckAccessResponse.newBuilder()
                .setAllowed(decision.allowed())
                .setReason(decision.reason() == null ? "" : decision.reason().name())
                .build();
    }

    private static String emptyAsMissing(String value) {
        return value.isEmpty() ? null : value;
    }

    private static String snakeCase(String field) {
        return field.replaceAll("([A-Z])", "_$1").toLowerCase(Locale.ROOT);
    }
}
