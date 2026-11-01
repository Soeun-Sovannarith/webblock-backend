package org.kshrd.webblock.project.application.wizard.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import java.util.regex.Pattern;
import org.kshrd.webblock.project.application.wizard.request.OpeningHourRequest;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/**
 * Only the order is checked here: a time that is not "HH:mm" is the field's own @Pattern error, and "HH:mm" strings
 * order the same as the times they name.
 */
public final class OpeningRowValidator implements ConstraintValidator<OpeningRow, OpeningHourRequest> {

    private static final Pattern TIME = Pattern.compile(OpeningHourRequest.TIME);

    @Override
    public boolean isValid(OpeningHourRequest row, ConstraintValidatorContext context) {
        if (row == null || !row.open()) {
            return true;
        }
        context.disableDefaultConstraintViolation();
        boolean valid = true;
        if (row.opensAt() == null) {
            valid = report(context, "opensAt", ValidationCodes.FIELD_REQUIRED);
        }
        if (row.closesAt() == null) {
            valid = report(context, "closesAt", ValidationCodes.FIELD_REQUIRED);
        }
        if (valid
                && isTime(row.opensAt())
                && isTime(row.closesAt())
                && row.opensAt().compareTo(row.closesAt()) >= 0) {
            valid = report(context, "closesAt", ValidationCodes.VALUE_INVALID);
        }
        return valid;
    }

    private static boolean isTime(String value) {
        return TIME.matcher(value).matches();
    }

    private static boolean report(ConstraintValidatorContext context, String field, String code) {
        context.buildConstraintViolationWithTemplate(code)
                .addPropertyNode(field)
                .addConstraintViolation();
        return false;
    }
}
