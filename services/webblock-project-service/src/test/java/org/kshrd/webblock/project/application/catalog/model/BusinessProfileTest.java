package org.kshrd.webblock.project.application.catalog.model;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class BusinessProfileTest {

    @Test
    void toStringShowsNoPersonalData() {
        BusinessProfile profile = new BusinessProfile(
                "Sokha's shop", "Private description", "+855 12 345 678", "sokha@example.test", "Street 1", "UTC");
        assertThat(profile.toString()).doesNotContain("Sokha", "Private", "855", "sokha@example.test", "Street", "UTC");
    }

    @Test
    void toStringTellsWhichOptionalFieldsAreSet() {
        assertThat(new BusinessProfile("Shop", null, "012", null, null, "Asia/Phnom_Penh").toString())
                .isEqualTo("BusinessProfile[businessName=<redacted>, description=null, phone=<redacted>, email=null,"
                        + " address=null, timezone=<redacted>]");
        assertThat(new BusinessProfile("Shop", null, null, null, null, null).toString())
                .endsWith("timezone=null]");
    }
}
