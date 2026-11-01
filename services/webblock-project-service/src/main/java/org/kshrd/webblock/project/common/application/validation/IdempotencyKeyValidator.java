package org.kshrd.webblock.project.common.application.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import java.nio.charset.StandardCharsets;

public final class IdempotencyKeyValidator implements ConstraintValidator<IdempotencyKey, String> {

    static final int MAX_BYTES = 255;

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) {
            return true;
        }
        if (value.isEmpty() || value.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) {
            return false;
        }
        if (!value.equals(value.strip())) {
            return false;
        }
        return value.codePoints().noneMatch(Character::isISOControl);
    }
}
