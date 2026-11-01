package org.kshrd.webblock.project.application.wizard.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import java.time.ZoneId;
import java.util.Set;

/**
 * Null and blank are left to @NotBlank (one code per field). The 100-character bound is the varchar(100) of
 * catalog.business_profiles.timezone.
 */
public final class TimeZoneIdValidator implements ConstraintValidator<TimeZoneId, String> {

    static final int MAX_LENGTH = 100;

    /** Read once: ZoneId.getAvailableZoneIds() copies the JDK's region IDs into a new set on every call. */
    private static final Set<String> ZONES = Set.copyOf(ZoneId.getAvailableZoneIds());

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        return value == null || value.isBlank() || (value.length() <= MAX_LENGTH && ZONES.contains(value));
    }
}
