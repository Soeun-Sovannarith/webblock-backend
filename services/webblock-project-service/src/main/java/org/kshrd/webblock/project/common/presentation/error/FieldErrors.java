package org.kshrd.webblock.project.common.presentation.error;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Path;
import java.util.Map;
import java.util.Optional;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;

/**
 * The one rule that turns a Bean Validation failure into a field error, for REST ({@link GlobalExceptionHandler}) and
 * gRPC alike: the constraint's message when it is a field-level ErrorCode (a ValidationCodes constant), otherwise the
 * built-in constraint's kind, otherwise VALUE_INVALID. The default English text and the rejected value never leak.
 */
public final class FieldErrors {

    /** A built-in Bean Validation constraint's kind decides a field error's code when its message is not one. */
    private static final Map<String, ErrorCode> BY_VALIDATION_CONSTRAINT = Map.ofEntries(
            Map.entry("NotNull", ErrorCode.FIELD_REQUIRED),
            Map.entry("NotBlank", ErrorCode.FIELD_REQUIRED),
            Map.entry("NotEmpty", ErrorCode.FIELD_REQUIRED),
            Map.entry("Size", ErrorCode.FIELD_LENGTH_INVALID),
            Map.entry("Min", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("Max", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("DecimalMin", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("DecimalMax", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("Positive", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("PositiveOrZero", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("Negative", ErrorCode.FIELD_OUT_OF_RANGE),
            Map.entry("NegativeOrZero", ErrorCode.FIELD_OUT_OF_RANGE));

    private FieldErrors() {}

    /** The field error of a constraint, given its message and the constraint's simple name (null when unknown). */
    public static ErrorDetail of(String field, String message, String constraint) {
        ErrorCode code = ErrorCode.fromCode(message)
                .filter(ErrorCode::isFieldCode)
                .or(() -> Optional.ofNullable(constraint).map(BY_VALIDATION_CONSTRAINT::get))
                .orElse(ErrorCode.VALUE_INVALID);
        return ErrorDetail.of(field, code);
    }

    /** The field error of a violation, named by the last node of its property path. */
    public static ErrorDetail of(ConstraintViolation<?> violation) {
        String constraint = violation
                .getConstraintDescriptor()
                .getAnnotation()
                .annotationType()
                .getSimpleName();
        return of(lastNode(violation.getPropertyPath()), violation.getMessage(), constraint);
    }

    private static String lastNode(Path path) {
        String name = "value";
        for (Path.Node node : path) {
            if (node.getName() != null) {
                name = node.getName();
            }
        }
        return name;
    }
}
