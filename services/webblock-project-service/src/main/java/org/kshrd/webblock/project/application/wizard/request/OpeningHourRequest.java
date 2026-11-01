package org.kshrd.webblock.project.application.wizard.request;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.time.DayOfWeek;
import org.kshrd.webblock.project.application.catalog.model.OpeningHour;
import org.kshrd.webblock.project.application.wizard.validation.OpeningRow;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/**
 * One weekday of the wizard's opening hours. The times are 24-hour "HH:mm"; a closed day may carry any valid time,
 * which is ignored. An open day needs both, and it opens before it closes (a day never closes after midnight).
 */
@OpeningRow
public record OpeningHourRequest(
        @NotNull DayOfWeek day,
        boolean open,

        @Pattern(regexp = TIME, message = ValidationCodes.VALUE_INVALID)
        String opensAt,

        @Pattern(regexp = TIME, message = ValidationCodes.VALUE_INVALID)
        String closesAt) {

    public static final String TIME = "^([01][0-9]|2[0-3]):[0-5][0-9]$";

    OpeningHour toOpeningHour() {
        return new OpeningHour(day, open, opensAt, closesAt);
    }
}
