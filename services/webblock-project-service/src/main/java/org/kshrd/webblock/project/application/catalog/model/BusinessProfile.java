package org.kshrd.webblock.project.application.catalog.model;

import java.util.List;

/**
 * The wizard's business profile, written to catalog.business_profiles. It waits in
 * project_messaging.pending_catalog_registrations until then, and never sits in an outbox row: outbox rows are kept
 * after completion, and personal data must not stay behind once the catalog holds it.
 */
public record BusinessProfile(
        String businessName,
        String description,
        String phone,
        String email,
        String address,
        String timezone,
        List<OpeningRange> openingHours) {

    /** A pending row written before opening hours existed has no such field; it registers with none. */
    public BusinessProfile {
        openingHours = openingHours == null ? List.of() : List.copyOf(openingHours);
    }

    public BusinessProfile(
            String businessName, String description, String phone, String email, String address, String timezone) {
        this(businessName, description, phone, email, address, timezone, List.of());
    }

    private static final String REDACTED = "<redacted>";

    /**
     * Personal data never reaches a log through this: only which fields are set. The time zone is redacted too: an
     * invalid request's time zone is unvalidated client text.
     */
    @Override
    public String toString() {
        return "BusinessProfile[businessName=" + redacted(businessName)
                + ", description=" + redacted(description)
                + ", phone=" + redacted(phone)
                + ", email=" + redacted(email)
                + ", address=" + redacted(address)
                + ", timezone=" + redacted(timezone) + "]";
    }

    private static String redacted(String value) {
        return value == null ? "null" : REDACTED;
    }
}
