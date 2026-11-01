package org.kshrd.webblock.project.presentation.rest.wizard;

import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.project.handler.ProjectViewAssembler;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.project.model.ProjectView;
import org.kshrd.webblock.project.application.wizard.handler.ProjectCreationService;
import org.kshrd.webblock.project.application.wizard.model.CreateProjectCommand;
import org.kshrd.webblock.project.application.wizard.model.CreationOutcome;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = ProjectCreationController.class)
@Import(WebSliceSupport.Imports.class)
class ProjectCreationControllerTest {

    static final UUID OWNER = UUID.randomUUID();
    static final UUID PROJECT = UUID.randomUUID();

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    ProjectCreationService service;

    @MockitoBean
    ProjectViewAssembler views;

    @BeforeEach
    void viewsOfTheRecord() {
        when(views.view(any()))
                .thenAnswer(call ->
                        ProjectView.of(call.<ProjectRecord>getArgument(0), "food_shop", "Foods", "/t.png", null));
    }

    static String body(String businessName, String timezone, String email) {
        return """
                {"name":"My shop","subdomain":"kilncoffee","businessTypeCode":"clothing_shop","templateCode":"blank",
                 "businessProfile":{"businessName":%s,"description":null,"phone":null,"email":%s,
                                    "address":null,"timezone":%s}}""".formatted(businessName, email, timezone);
    }

    static String body(String timezone, String email) {
        return body("\"My shop\"", "\"" + timezone + "\"", email);
    }

    private void outcome(boolean created) {
        Instant t = Instant.parse("2026-10-15T00:00:00Z");
        when(service.create(eq(OWNER), eq(PROJECT), any()))
                .thenReturn(new CreationOutcome(
                        new ProjectRecord(
                                PROJECT,
                                OWNER,
                                "My shop",
                                "kilncoffee",
                                UUID.randomUUID(),
                                0,
                                "0.1.0",
                                ProjectStatus.draft,
                                "kilncoffee.sites.webblock.test",
                                t,
                                t,
                                null),
                        created));
    }

    @Test
    @DisplayName("FR-13.1: 201 with subdomain and platformHostname; a repeat is 200")
    void fr13_1_aNewProjectIs201AndARepeatIs200() throws Exception {
        outcome(true);
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("Asia/Phnom_Penh", "\"shop@example.test\"")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.projectId").value(PROJECT.toString()))
                .andExpect(jsonPath("$.data.subdomain").value("kilncoffee"))
                .andExpect(jsonPath("$.data.platformHostname").value("kilncoffee.sites.webblock.test"))
                .andExpect(jsonPath("$.data.ownerUserId").doesNotExist())
                .andExpect(jsonPath("$.data.businessTypeCode").value("food_shop"))
                .andExpect(jsonPath("$.data.businessTypeName").value("Foods"))
                .andExpect(jsonPath("$.data.thumbnailUrl").value("/t.png"));
        verify(service)
                .create(
                        OWNER,
                        PROJECT,
                        new CreateProjectCommand(
                                "My shop",
                                "kilncoffee",
                                "clothing_shop",
                                "blank",
                                new BusinessProfile(
                                        "My shop", null, null, "shop@example.test", null, "Asia/Phnom_Penh")));
        outcome(false);
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("Asia/Phnom_Penh", "null")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.projectId").value(PROJECT.toString()));
    }

    @Test
    void anUnknownTimeZoneAndABadEmailAreFieldErrors() throws Exception {
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("Mars/Olympus", "\"not-an-email\"")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[?(@.field == 'businessProfile.timezone')].code")
                        .value("VALUE_INVALID"))
                .andExpect(jsonPath("$.errors[?(@.field == 'businessProfile.email')].code")
                        .value("VALUE_INVALID"));
        verifyNoInteractions(service);
    }

    /** The catalog's varchar(255) and varchar(100) columns are refused here, not later; blank values get defaults. */
    @Test
    @DisplayName("FR-10.13: the business name and time zone fit their catalog columns")
    void fr10_13_theBusinessNameAndTimeZoneFitTheirCatalogColumns() throws Exception {
        expectFieldError(
                body("\"" + "a".repeat(256) + "\"", "\"UTC\"", "null"),
                "businessProfile.businessName",
                "FIELD_LENGTH_INVALID");
        expectFieldError(
                body("\"Shop\"", "\"Asia/" + "x".repeat(96) + "\"", "null"),
                "businessProfile.timezone",
                "VALUE_INVALID");
        verifyNoInteractions(service);
    }

    @Test
    @DisplayName("FR-10.13: a missing or blank business name and time zone become the website name and Asia/Phnom_Penh")
    void fr10_13_blankProfileValuesGetDefaults() throws Exception {
        outcome(true);
        for (String[] pair : new String[][] {{"null", "null"}, {"\"   \"", "\"\""}}) {
            mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body(pair[0], pair[1], "null")))
                    .andExpect(status().isCreated());
        }
        verify(service, org.mockito.Mockito.times(2))
                .create(
                        OWNER,
                        PROJECT,
                        new CreateProjectCommand(
                                "My shop",
                                "kilncoffee",
                                "clothing_shop",
                                "blank",
                                new BusinessProfile("My shop", null, null, null, null, "Asia/Phnom_Penh")));
    }

    @Test
    void theLongestAcceptedValuesReachTheService() throws Exception {
        outcome(true);
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("\"" + "a".repeat(255) + "\"", "\"America/Argentina/Buenos_Aires\"", "null")))
                .andExpect(status().isCreated());
    }

    @Test
    void aMissingBusinessProfileOrNameIsRequired() throws Exception {
        expectFieldError("""
                {"name":"My shop","subdomain":"kilncoffee","businessTypeCode":"clothing_shop","templateCode":"blank"}""", "businessProfile", "FIELD_REQUIRED");
        expectFieldError(
                body("UTC", "null").replace("\"name\":\"My shop\"", "\"name\":\" \""), "name", "FIELD_REQUIRED");
        verifyNoInteractions(service);
    }

    private static String bodyWithHours(String hoursJson) {
        return """
                {"name":"My shop","subdomain":"kilncoffee","businessTypeCode":"clothing_shop","templateCode":"blank",
                 "businessProfile":{"businessName":"My shop","timezone":"UTC","openingHours":%s}}""".formatted(hoursJson);
    }

    private static String week(String mondayRow) {
        return "[" + mondayRow
                + ",{\"day\":\"TUESDAY\",\"open\":true,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}"
                + ",{\"day\":\"WEDNESDAY\",\"open\":true,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}"
                + ",{\"day\":\"THURSDAY\",\"open\":false,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}"
                + ",{\"day\":\"FRIDAY\",\"open\":false,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}"
                + ",{\"day\":\"SATURDAY\",\"open\":false,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}"
                + ",{\"day\":\"SUNDAY\",\"open\":false,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}]";
    }

    @Test
    @DisplayName("FR-10.8: the seven opening-hour rows reach the service as catalog ranges")
    void fr10_8_openingHoursReachTheServiceAsRanges() throws Exception {
        outcome(true);
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(bodyWithHours(week(
                                "{\"day\":\"MONDAY\",\"open\":true,\"opensAt\":\"09:00\",\"closesAt\":\"18:00\"}"))))
                .andExpect(status().isCreated());
        verify(service)
                .create(
                        eq(OWNER),
                        eq(PROJECT),
                        argThat(c -> c.businessProfile()
                                .openingHours()
                                .equals(List.of(new org.kshrd.webblock.project.application.catalog.model.OpeningRange(
                                        "MONDAY", "WEDNESDAY", "09:00", "18:00", false)))));
    }

    @Test
    @DisplayName("FR-10.9: six rows, or a row that closes before it opens, are 400 VALIDATION_FAILED with the field")
    void fr10_9_badOpeningHoursAreFieldErrors() throws Exception {
        expectFieldError(
                bodyWithHours("[{\"day\":\"MONDAY\",\"open\":false,\"opensAt\":null,\"closesAt\":null}]"),
                "businessProfile.openingHours",
                "VALUE_INVALID");
        expectFieldError(
                bodyWithHours(week("{\"day\":\"MONDAY\",\"open\":true,\"opensAt\":\"18:00\",\"closesAt\":\"09:00\"}")),
                "businessProfile.openingHours[0].closesAt",
                "VALUE_INVALID");
        verifyNoInteractions(service);
    }

    @Test
    @DisplayName("FR-13.3: a missing subdomain is FIELD_REQUIRED and reaches no service")
    void fr13_3_aMissingSubdomainIsRequired() throws Exception {
        expectFieldError(
                body("UTC", "null").replace("\"subdomain\":\"kilncoffee\",", ""), "subdomain", "FIELD_REQUIRED");
        expectFieldError(body("UTC", "null").replace("\"kilncoffee\"", "null"), "subdomain", "FIELD_REQUIRED");
        verifyNoInteractions(service);
    }

    /** The rule itself runs in the service (ProjectService.requireValidSubdomain); this is its answer on the wire. */
    @Test
    @DisplayName(
            "FR-13.3: a refusal from the service is written as 400 VALIDATION_FAILED, field subdomain VALUE_INVALID")
    void fr13_3_aRefusalIsWrittenAs400WithItsField() throws Exception {
        for (String bad : List.of("auth", "-shop", "ab")) {
            when(service.create(eq(OWNER), eq(PROJECT), argThat(c -> c != null && bad.equals(c.subdomain()))))
                    .thenThrow(new WebBlockException(
                            ErrorCode.VALIDATION_FAILED,
                            List.of(ErrorDetail.of("subdomain", ErrorCode.VALUE_INVALID))));
            mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body("UTC", "null").replace("kilncoffee", bad)))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors[0].field").value("subdomain"))
                    .andExpect(jsonPath("$.errors[0].code").value("VALUE_INVALID"));
        }
    }

    @Test
    @DisplayName("FR-13.2: a taken sub-domain is 409 HOSTNAME_TAKEN")
    void fr13_2_aTakenSubdomainIs409() throws Exception {
        when(service.create(eq(OWNER), eq(PROJECT), any())).thenThrow(new WebBlockException(ErrorCode.HOSTNAME_TAKEN));
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("UTC", "null")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("HOSTNAME_TAKEN"))
                .andExpect(header().doesNotExist(HttpHeaders.RETRY_AFTER));
    }

    private void expectFieldError(String content, String field, String code) throws Exception {
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(content))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(
                        jsonPath("$.errors[?(@.field == '" + field + "')].code").value(code));
    }

    @Test
    void theNilUuidIsRefused() throws Exception {
        mvc.perform(put("/api/v1/projects/00000000-0000-0000-0000-000000000000")
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("UTC", "null")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("projectId"))
                .andExpect(jsonPath("$.errors[0].code").value("UUID_INVALID"));
        verifyNoInteractions(service);
    }

    @Test
    void anUnknownPropertyIsMalformed() throws Exception {
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("UTC", "null").replace("\"name\"", "\"ownerUserId\":\"x\",\"name\"")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
        verifyNoInteractions(service);
    }

    static String profile(String phone, String email, String description, String address) {
        return """
                {"name":"My shop","subdomain":"kilncoffee","businessTypeCode":"clothing_shop","templateCode":"blank",
                 "businessProfile":{"businessName":"My shop","description":%s,"phone":%s,"email":%s,
                                    "address":%s,"timezone":"UTC"}}""".formatted(description, phone, email, address);
    }

    @Test
    void blankOptionalProfileFieldsReachTheServiceAsNull() throws Exception {
        outcome(true);
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(profile("\"\"", "\"\"", "\"  \"", "\"\\n\\t\"")))
                .andExpect(status().isCreated());
        verify(service)
                .create(
                        OWNER,
                        PROJECT,
                        new CreateProjectCommand(
                                "My shop",
                                "kilncoffee",
                                "clothing_shop",
                                "blank",
                                new BusinessProfile("My shop", null, null, null, null, "UTC")));
    }

    @Test
    void aPhoneHasDigitsAndOnlyPhoneCharacters() throws Exception {
        outcome(true);
        for (String phone : List.of("+855 (0)12-345.678", "012345678", "  ")) {
            mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                            .with(Users.user(OWNER))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(profile("\"" + phone + "\"", "null", "null", "null")))
                    .andExpect(status().isCreated());
        }
        for (String phone : List.of("<b>x</b>", "012\\n345", "012\\u0007", "call me", "+-().", "0".repeat(51))) {
            expectFieldError(
                    profile("\"" + phone + "\"", "null", "null", "null"),
                    "businessProfile.phone",
                    phone.length() > 50 ? "FIELD_LENGTH_INVALID" : "VALUE_INVALID");
        }
    }

    /** Review R1-H1: the largest phone a body can carry is refused quickly through the whole request path. */
    @Test
    void aPhoneAsLargeAsTheBodyLimitIsRefusedQuickly() throws Exception {
        String phone = "1".repeat(1_500_000) + "x";
        // Warm-up, outside the timer: one small request through the same path (R2-L1).
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(profile("\"12x\"", "null", "null", "null")))
                .andExpect(status().isBadRequest());
        assertTimeoutPreemptively(
                Duration.ofSeconds(5),
                () -> mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                                .with(Users.user(OWNER))
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(profile("\"" + phone + "\"", "null", "null", "null")))
                        .andExpect(status().isBadRequest())
                        .andExpect(jsonPath(
                                "$.errors[?(@.field == 'businessProfile.phone')].code",
                                org.hamcrest.Matchers.containsInAnyOrder("FIELD_LENGTH_INVALID", "VALUE_INVALID"))));
        verifyNoInteractions(service);
    }

    @Test
    void aPendingAdmissionReleaseIs409WithRetryAfter() throws Exception {
        when(service.create(eq(OWNER), eq(PROJECT), any()))
                .thenThrow(new WebBlockException(ErrorCode.ADMISSION_RELEASE_PENDING));
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("UTC", "null")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ADMISSION_RELEASE_PENDING"))
                .andExpect(header().string(HttpHeaders.RETRY_AFTER, "5"));
    }

    @Test
    void anotherConflictCarriesNoRetryAfter() throws Exception {
        when(service.create(eq(OWNER), eq(PROJECT), any()))
                .thenThrow(new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT));
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .with(Users.user(OWNER))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("UTC", "null")))
                .andExpect(status().isConflict())
                .andExpect(header().doesNotExist(HttpHeaders.RETRY_AFTER));
    }

    @Test
    void anAnonymousCallerIs401() throws Exception {
        mvc.perform(put("/api/v1/projects/{id}", PROJECT)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("UTC", "null")))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(service);
    }
}
