package org.kshrd.webblock.project.application.wizard.request;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.catalog.model.OpeningHours;
import org.kshrd.webblock.project.application.wizard.validation.OpeningWeek;
import org.kshrd.webblock.project.application.wizard.validation.TimeZoneId;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/**
 * Limits follow catalog.business_profiles (revision 19 section 6), so the catalog registration job can never fail on
 * them later; description and address, TEXT there, are capped here. @Size counts UTF-16 units, never
 * fewer than PostgreSQL's characters, so a value within it fits its varchar column. A blank optional field is stored
 * as NULL, never as an empty string. A blank business name becomes the website's name and a blank time zone
 * {@value #DEFAULT_TIMEZONE} (FR-10.13).
 */
public record BusinessProfileRequest(
        @Size(max = 255) String businessName,
        @Size(max = 5000) String description,

        @Size(max = 50) @Pattern(regexp = PHONE, message = ValidationCodes.VALUE_INVALID)
        String phone,

        @Email @Size(max = 320) String email,
        @Size(max = 1000) String address,
        @TimeZoneId String timezone,
        @Valid @OpeningWeek List<OpeningHourRequest> openingHours) {

    static final String DEFAULT_TIMEZONE = "Asia/Phnom_Penh";

    /** A client that sends no opening hours leaves them empty in the catalog. */
    public BusinessProfileRequest(
            String businessName, String description, String phone, String email, String address, String timezone) {
        this(businessName, description, phone, email, address, timezone, null);
    }

    /**
     * Blank, or at least one digit among digits, spaces and + - ( ) . only. Linear: every quantifier is
     * possessive and the digit is required by a lookahead, so nothing backtracks. Hibernate Validator runs it even when
     * {@code @Size} has failed, on a value as long as the request body.
     */
    static final String PHONE = "^\\s*+$|^(?=[^0-9]*+[0-9])[0-9+()\\-. ]*+$";

    BusinessProfile toProfile() {
        return toProfile(null);
    }

    BusinessProfile toProfile(String websiteName) {
        List<OpeningHourRequest> week = openingHours == null ? List.of() : openingHours;
        return new BusinessProfile(
                orNull(businessName) == null && websiteName != null ? websiteName.strip() : orNull(businessName),
                orNull(description),
                orNull(phone),
                orNull(email),
                orNull(address),
                orNull(timezone) == null ? DEFAULT_TIMEZONE : timezone,
                OpeningHours.toRanges(week.stream()
                        .filter(row -> row != null && row.day() != null)
                        .map(OpeningHourRequest::toOpeningHour)
                        .toList()));
    }

    /** Personal data never reaches a log through this (Spring MVC logs bodies at DEBUG); the profile redacts it. */
    @Override
    public String toString() {
        return "BusinessProfileRequest[" + toProfile() + "]";
    }

    private static String orNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
