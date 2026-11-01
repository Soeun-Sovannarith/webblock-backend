package org.kshrd.webblock.project.common.domain.error;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class J1ErrorCodesTest {

    private static final Map<String, ErrorCategory> EXPECTED = Map.ofEntries(
            Map.entry("PROJECT_NOT_FOUND", ErrorCategory.NOT_FOUND),
            Map.entry("PROJECT_ID_CONFLICT", ErrorCategory.CONFLICT),
            Map.entry("ADMISSION_RELEASE_PENDING", ErrorCategory.CONFLICT),
            Map.entry("PROJECT_READ_ONLY", ErrorCategory.BUSINESS_RULE),
            Map.entry("STALE_EDIT_REVISION", ErrorCategory.CONFLICT),
            Map.entry("PAGE_NOT_FOUND", ErrorCategory.NOT_FOUND),
            Map.entry("PAGE_LIMIT_REACHED", ErrorCategory.BUSINESS_RULE),
            Map.entry("PAGE_PATH_TAKEN", ErrorCategory.CONFLICT),
            Map.entry("PUCK_TREE_INVALID", ErrorCategory.VALIDATION),
            Map.entry("COMPONENT_CATALOG_NOT_FOUND", ErrorCategory.NOT_FOUND),
            Map.entry("BUSINESS_TYPE_NOT_FOUND", ErrorCategory.NOT_FOUND),
            Map.entry("BUSINESS_TYPE_NOT_AVAILABLE", ErrorCategory.BUSINESS_RULE),
            Map.entry("TEMPLATE_NOT_AVAILABLE", ErrorCategory.BUSINESS_RULE),
            Map.entry("WEBSITE_LIMIT_REACHED", ErrorCategory.BUSINESS_RULE),
            Map.entry("TRIAL_ENDED", ErrorCategory.BUSINESS_RULE),
            Map.entry("NO_ACTIVE_PLAN", ErrorCategory.BUSINESS_RULE),
            Map.entry("CLIENT_SUSPENDED", ErrorCategory.BUSINESS_RULE),
            Map.entry("WEBSITE_NOT_ADMITTED", ErrorCategory.BUSINESS_RULE),
            Map.entry("PAYMENT_UNAVAILABLE", ErrorCategory.DEPENDENCY),
            Map.entry("CATALOG_UNAVAILABLE", ErrorCategory.DEPENDENCY),
            Map.entry("PROJECT_NAME_TAKEN", ErrorCategory.CONFLICT),
            Map.entry("TEMPLATE_NOT_FOUND", ErrorCategory.NOT_FOUND),
            Map.entry("VERSION_NOT_FOUND", ErrorCategory.NOT_FOUND),
            Map.entry("HOSTNAME_TAKEN", ErrorCategory.CONFLICT));

    @Test
    void everyJ1CodeExistsWithItsCategoryAndAFixedSentence() {
        EXPECTED.forEach((name, category) -> {
            ErrorCode code = ErrorCode.fromCode(name).orElseThrow(() -> new AssertionError("missing " + name));
            assertThat(code.category()).as(name).isEqualTo(category);
            assertThat(code.message()).as(name).isNotBlank().endsWith(".").doesNotContain("%s", "{");
        });
    }

    @Test
    @DisplayName("FR-36.5: DOMAIN_TERMINAL is gone with project.domains")
    void fr36_5_domainTerminalIsGone() {
        assertThat(ErrorCode.fromCode("DOMAIN_TERMINAL")).isEmpty();
    }

    @Test
    @DisplayName("FR-13.2: HOSTNAME_TAKEN is a 409 about the sub-domain")
    void fr13_2_hostnameTakenNamesTheSubdomain() {
        assertThat(ErrorCode.HOSTNAME_TAKEN.category()).isEqualTo(ErrorCategory.CONFLICT);
        assertThat(ErrorCode.HOSTNAME_TAKEN.message()).isEqualTo("This sub-domain is already taken.");
    }

    @Test
    void puckTreeInvalidIsAFieldCodeWithAValidationConstant() {
        assertThat(ErrorCode.PUCK_TREE_INVALID.isFieldCode()).isTrue();
        assertThat(ValidationCodes.PUCK_TREE_INVALID).isEqualTo("PUCK_TREE_INVALID");
    }
}
