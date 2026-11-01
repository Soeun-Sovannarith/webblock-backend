package org.kshrd.webblock.project.application.wizard.request;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;

class BusinessProfileRequestTest {

    private static final ValidatorFactory FACTORY = Validation.buildDefaultValidatorFactory();
    private static final Validator VALIDATOR = FACTORY.getValidator();

    @AfterAll
    static void close() {
        FACTORY.close();
    }

    private static Set<String> phoneViolations(String phone) {
        return violations(new BusinessProfileRequest("Shop", null, phone, null, null, "UTC"), "phone");
    }

    /** The simple names of the constraints a property violates. */
    private static Set<String> violations(BusinessProfileRequest request, String property) {
        Set<ConstraintViolation<BusinessProfileRequest>> violations = VALIDATOR.validateProperty(request, property);
        return Set.copyOf(violations.stream()
                .map(v -> v.getConstraintDescriptor()
                        .getAnnotation()
                        .annotationType()
                        .getSimpleName())
                .toList());
    }

    @Test
    void thePhonePatternAcceptsAndRefusesWhatTheRuleSays() {
        Pattern phone = Pattern.compile(BusinessProfileRequest.PHONE);
        for (String accepted :
                List.of("", " ", " \n\t", "+855 (0)12-345.678", "012345678", "1", "(+)1", "0".repeat(51))) {
            assertThat(phone.matcher(accepted).matches()).as("'%s'", accepted).isTrue();
        }
        for (String refused : List.of("<b>x</b>", "call me", "+-().", "012\n345", "012\u0007", "12x", "x12", " 1 a")) {
            assertThat(phone.matcher(refused).matches()).as("'%s'", refused).isFalse();
        }
        assertThat(phoneViolations("0".repeat(51))).containsExactly("Size");
        assertThat(phoneViolations("call me")).containsExactly("Pattern");
    }

    /**
     * Review R1-H1: the pattern runs even when @Size has already failed, and a request body may hold about 1.5 M
     * characters. The earlier pattern backtracked quadratically on digits followed by an invalid character (an 80 KB
     * body took 18 s); this one is linear.
     */
    @Test
    void aHugePhoneIsRefusedInLinearTime() {
        String digitsThenJunk = "1".repeat(1_500_000) + "x";
        String spacesThenJunk = " ".repeat(1_500_000) + "x";
        assertThat(phoneViolations("12x")).containsExactly("Pattern"); // warm-up, outside the timer
        // The bound is far below the 18 s the old pattern took on 80 KB, with room for a loaded single core (R2-L1).
        assertTimeoutPreemptively(Duration.ofSeconds(5), () -> {
            assertThat(phoneViolations(digitsThenJunk)).containsExactlyInAnyOrder("Size", "Pattern");
            assertThat(phoneViolations(spacesThenJunk)).containsExactlyInAnyOrder("Size", "Pattern");
        });
    }

    /** The audit that followed R1-H1: Hibernate's @Email also sees request input of any length. */
    @Test
    void aHugeEmailIsRefusedInLinearTime() {
        List<String> emails = List.of(
                "a".repeat(1_500_000) + "@example.test",
                "a@" + "b.".repeat(750_000) + "x",
                "a@" + "b-".repeat(750_000) + "!",
                "a.".repeat(750_000) + "@x");
        assertThat(violations(new BusinessProfileRequest("Shop", null, null, "a@", null, "UTC"), "email"))
                .containsExactly("Email"); // warm-up, outside the timer
        assertTimeoutPreemptively(Duration.ofSeconds(5), () -> {
            for (String email : emails) {
                assertThat(violations(new BusinessProfileRequest("Shop", null, null, email, null, "UTC"), "email"))
                        .contains("Size");
            }
        });
    }

    /** Spring MVC logs resolved bodies and rejected values at DEBUG: personal data must not be in them. */
    @Test
    void theRequestsNeverPrintPersonalData() {
        BusinessProfileRequest profile = new BusinessProfileRequest(
                "Sokha Clothing", "Run by Sokha Chan", "+855 12 345 678", "sokha@example.test", "12 Street 51", "UTC");
        CreateProjectRequest create =
                new CreateProjectRequest("My shop", "kilncoffee", "clothing_shop", "blank", profile);
        for (String printed : List.of(profile.toString(), create.toString())) {
            assertThat(printed)
                    .doesNotContain("Sokha")
                    .doesNotContain("345")
                    .doesNotContain("example.test")
                    .doesNotContain("Street")
                    .doesNotContain("UTC");
        }
    }
}
