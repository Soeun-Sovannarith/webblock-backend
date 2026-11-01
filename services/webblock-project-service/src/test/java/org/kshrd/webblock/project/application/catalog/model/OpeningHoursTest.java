package org.kshrd.webblock.project.application.catalog.model;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.DayOfWeek;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class OpeningHoursTest {

    private static OpeningHour open(DayOfWeek day, String from, String to) {
        return new OpeningHour(day, true, from, to);
    }

    private static OpeningHour closed(DayOfWeek day) {
        return new OpeningHour(day, false, "09:00", "17:00");
    }

    private static List<OpeningHour> week(OpeningHour... days) {
        return List.of(days);
    }

    private static final List<OpeningHour> MON_FRI_SAT_SUN_CLOSED = week(
            open(DayOfWeek.MONDAY, "09:00", "18:00"),
            open(DayOfWeek.TUESDAY, "09:00", "18:00"),
            open(DayOfWeek.WEDNESDAY, "09:00", "18:00"),
            open(DayOfWeek.THURSDAY, "09:00", "18:00"),
            open(DayOfWeek.FRIDAY, "09:00", "18:00"),
            closed(DayOfWeek.SATURDAY),
            closed(DayOfWeek.SUNDAY));

    @Test
    @DisplayName("FR-10.8: Monday to Friday with the same hours is one range, in the catalog's names")
    void fr10_8_mondayToFridayIsOneRange() {
        assertThat(OpeningHours.toRanges(MON_FRI_SAT_SUN_CLOSED))
                .containsExactly(new OpeningRange("MONDAY", "FRIDAY", "09:00", "18:00", false));
    }

    @Test
    @DisplayName("FR-10.8: a split week gives one range per run of equal hours; the row order does not matter")
    void fr10_8_aSplitWeekGivesOneRangePerRun() {
        List<OpeningHour> split = new ArrayList<>(week(
                open(DayOfWeek.MONDAY, "09:00", "17:00"),
                open(DayOfWeek.TUESDAY, "09:00", "17:00"),
                open(DayOfWeek.WEDNESDAY, "10:00", "17:00"),
                closed(DayOfWeek.THURSDAY),
                open(DayOfWeek.FRIDAY, "09:00", "17:00"),
                open(DayOfWeek.SATURDAY, "09:00", "17:00"),
                open(DayOfWeek.SUNDAY, "09:00", "17:00")));
        Collections.reverse(split);
        assertThat(OpeningHours.toRanges(split))
                .containsExactly(
                        new OpeningRange("MONDAY", "TUESDAY", "09:00", "17:00", false),
                        new OpeningRange("WEDNESDAY", "WEDNESDAY", "10:00", "17:00", false),
                        new OpeningRange("FRIDAY", "SUNDAY", "09:00", "17:00", false));
    }

    @Test
    @DisplayName("FR-10.8: a week that is closed every day has no range")
    void fr10_8_allClosedIsEmpty() {
        List<OpeningHour> allClosed = new ArrayList<>();
        for (DayOfWeek day : DayOfWeek.values()) {
            allClosed.add(closed(day));
        }
        assertThat(OpeningHours.toRanges(allClosed)).isEmpty();
    }

    @Test
    @DisplayName("FR-10.8: Sunday alone is a range from SUNDAY to SUNDAY")
    void fr10_8_sundayOnly() {
        List<OpeningHour> sundayOnly = new ArrayList<>();
        for (DayOfWeek day : DayOfWeek.values()) {
            sundayOnly.add(day == DayOfWeek.SUNDAY ? open(day, "08:30", "12:00") : closed(day));
        }
        assertThat(OpeningHours.toRanges(sundayOnly))
                .containsExactly(new OpeningRange("SUNDAY", "SUNDAY", "08:30", "12:00", false));
    }

    @Test
    @DisplayName("FR-10.11: the two lines the home page shows follow the front end's wording")
    void fr10_11_theHomePageLines() {
        List<OpeningRange> ranges = OpeningHours.toRanges(MON_FRI_SAT_SUN_CLOSED);
        assertThat(OpeningHours.hoursText(ranges)).isEqualTo("9:00 – 18:00");
        assertThat(OpeningHours.detailText(ranges)).isEqualTo("Mon – Fri 9:00 – 18:00 · Sat – Sun closed");
        List<OpeningRange> split = List.of(
                new OpeningRange("MONDAY", "WEDNESDAY", "09:00", "17:00", false),
                new OpeningRange("FRIDAY", "FRIDAY", "10:30", "20:00", false));
        assertThat(OpeningHours.detailText(split))
                .isEqualTo("Mon – Wed 9:00 – 17:00 · Thu closed · Fri 10:30 – 20:00 · Sat – Sun closed");
        assertThat(OpeningHours.hoursText(List.of())).isEqualTo("Closed");
    }
}
