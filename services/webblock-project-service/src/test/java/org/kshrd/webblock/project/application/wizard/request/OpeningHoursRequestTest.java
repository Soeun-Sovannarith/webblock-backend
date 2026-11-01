package org.kshrd.webblock.project.application.wizard.request;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import java.time.DayOfWeek;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.catalog.model.OpeningRange;

class OpeningHoursRequestTest {

    private static final ValidatorFactory FACTORY = Validation.buildDefaultValidatorFactory();
    private static final Validator VALIDATOR = FACTORY.getValidator();

    @AfterAll
    static void close() {
        FACTORY.close();
    }

    private static OpeningHourRequest open(DayOfWeek day, String from, String to) {
        return new OpeningHourRequest(day, true, from, to);
    }

    private static List<OpeningHourRequest> week() {
        List<OpeningHourRequest> week = new ArrayList<>();
        for (DayOfWeek day : DayOfWeek.values()) {
            week.add(
                    day.getValue() <= 5
                            ? open(day, "09:00", "18:00")
                            : new OpeningHourRequest(day, false, "09:00", "17:00"));
        }
        return week;
    }

    private static BusinessProfileRequest request(List<OpeningHourRequest> openingHours) {
        return new BusinessProfileRequest("Shop", null, null, null, null, "UTC", openingHours);
    }

    /** "path: message" of each violation, for example "openingHours[2].closesAt: VALUE_INVALID". */
    private static Set<String> violations(List<OpeningHourRequest> openingHours) {
        return VALIDATOR.validate(request(openingHours)).stream()
                .map((ConstraintViolation<BusinessProfileRequest> v) -> v.getPropertyPath() + ": " + v.getMessage())
                .collect(Collectors.toSet());
    }

    @Test
    @DisplayName("FR-10.8: a valid week becomes the profile's ranges")
    void fr10_8_aValidWeekIsAccepted() {
        assertThat(violations(week())).isEmpty();
        assertThat(request(week()).toProfile().openingHours())
                .containsExactly(new OpeningRange("MONDAY", "FRIDAY", "09:00", "18:00", false));
    }

    @Test
    @DisplayName("FR-10.8: no opening hours is allowed and gives none")
    void fr10_8_noOpeningHoursGivesNone() {
        assertThat(violations(null)).isEmpty();
        BusinessProfile profile = request(null).toProfile();
        assertThat(profile.openingHours()).isEmpty();
    }

    @Test
    @DisplayName("FR-10.9: six rows are refused")
    void fr10_9_sixRowsAreRefused() {
        assertThat(violations(week().subList(0, 6))).containsExactly("openingHours: VALUE_INVALID");
    }

    @Test
    @DisplayName("FR-10.9: a weekday twice is refused")
    void fr10_9_aDuplicateDayIsRefused() {
        List<OpeningHourRequest> twice = week();
        twice.set(6, open(DayOfWeek.MONDAY, "09:00", "18:00"));
        assertThat(violations(twice)).containsExactly("openingHours: VALUE_INVALID");
    }

    @Test
    @DisplayName("FR-10.9: an open day that opens at or after it closes is refused on closesAt")
    void fr10_9_opensNotBeforeClosesIsRefused() {
        List<OpeningHourRequest> bad = week();
        bad.set(2, open(DayOfWeek.WEDNESDAY, "18:00", "18:00"));
        bad.set(3, open(DayOfWeek.THURSDAY, "19:00", "08:00"));
        assertThat(violations(bad))
                .containsExactlyInAnyOrder(
                        "openingHours[2].closesAt: VALUE_INVALID", "openingHours[3].closesAt: VALUE_INVALID");
    }

    @Test
    @DisplayName(
            "FR-10.9: a time outside 00:00 to 23:59 is refused on its field; a closed day may carry any valid time")
    void fr10_9_aBadTimeIsRefused() {
        List<OpeningHourRequest> bad = week();
        bad.set(0, open(DayOfWeek.MONDAY, "24:00", "18:00"));
        bad.set(1, open(DayOfWeek.TUESDAY, "9:00", "18:60"));
        bad.set(2, open(DayOfWeek.WEDNESDAY, null, "18:00"));
        assertThat(violations(bad))
                .containsExactlyInAnyOrder(
                        "openingHours[0].opensAt: VALUE_INVALID",
                        "openingHours[1].opensAt: VALUE_INVALID",
                        "openingHours[1].closesAt: VALUE_INVALID",
                        "openingHours[2].opensAt: FIELD_REQUIRED");
    }

    @Test
    @DisplayName("FR-10.9: a row without a day is refused")
    void fr10_9_aRowWithoutADayIsRefused() {
        List<OpeningHourRequest> bad = week();
        bad.set(6, new OpeningHourRequest(null, false, null, null));
        assertThat(violations(bad)).contains("openingHours[6].day: must not be null");
    }
}
