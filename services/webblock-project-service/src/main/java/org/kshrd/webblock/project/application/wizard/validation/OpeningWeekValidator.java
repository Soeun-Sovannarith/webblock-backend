package org.kshrd.webblock.project.application.wizard.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import java.time.DayOfWeek;
import java.util.EnumSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import org.kshrd.webblock.project.application.wizard.request.OpeningHourRequest;

public final class OpeningWeekValidator implements ConstraintValidator<OpeningWeek, List<OpeningHourRequest>> {

    @Override
    public boolean isValid(List<OpeningHourRequest> rows, ConstraintValidatorContext context) {
        if (rows == null) {
            return true;
        }
        if (rows.size() != DayOfWeek.values().length || rows.stream().anyMatch(Objects::isNull)) {
            return false;
        }
        Set<DayOfWeek> days = EnumSet.noneOf(DayOfWeek.class);
        for (OpeningHourRequest row : rows) {
            if (row.day() == null || !days.add(row.day())) {
                return false;
            }
        }
        return true;
    }
}
