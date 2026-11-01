package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasItem;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@WebMvcTest(controllers = ProbeController.class)
@Import({WebSliceSupport.Imports.class, GlobalExceptionHandlerWebTest.ProbeConstraints.class})
class GlobalExceptionHandlerWebTest {

    @TestConfiguration
    static class ProbeConstraints {
        @Bean
        ConstraintErrorRegistration probeConstraints() {
            return () -> Map.of("probe_name_key", ErrorCode.CONCURRENT_UPDATE);
        }
    }

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @Test
    void invalidBodyListsEveryFieldWithItsCode() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"   \",\"path\":\"Bad\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.success").doesNotExist())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors[?(@.field=='name')].code").value(hasItem("FIELD_REQUIRED")))
                .andExpect(jsonPath("$.errors[?(@.field=='path')].code").value(hasItem("PAGE_PATH_INVALID")))
                .andExpect(content().string(not(containsString("Bad"))));
    }

    @Test
    void sizeViolationsReportFieldLengthInvalid() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"elevenchars\",\"path\":\"/a\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("name"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_LENGTH_INVALID"));
    }

    @Test
    void unknownPropertiesAreRefused() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"shop\",\"path\":\"/a\",\"admin\":true}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
    }

    @Test
    void scalarsAreNotCoercedIntoStrings() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":123,\"path\":\"/a\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
    }

    @Test
    void brokenJsonIsMalformed() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
    }

    @Test
    void missingHeaderIsAFieldError() throws Exception {
        mvc.perform(post("/api/test-probe/keyed").with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors[0].field").value("Idempotency-Key"))
                .andExpect(jsonPath("$.errors[0].code").value("FIELD_REQUIRED"));
    }

    @Test
    void invalidHeaderReportsItsAnnotationCode() throws Exception {
        mvc.perform(post("/api/test-probe/keyed").with(Users.anyUser()).header("Idempotency-Key", " padded"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("Idempotency-Key"))
                .andExpect(jsonPath("$.errors[0].code").value("IDEMPOTENCY_KEY_INVALID"));
    }

    /** Method validation reports a body's violations as ParameterErrors: each is named by its JSON field. */
    @Test
    void aBodyCheckedByMethodValidationNamesEachField() throws Exception {
        mvc.perform(post("/api/test-probe/keyed-body")
                        .with(Users.anyUser())
                        .header("Idempotency-Key", "key-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"   \",\"path\":\"Bad\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.errors.length()").value(2))
                .andExpect(jsonPath("$.errors[?(@.field=='name')].code").value(hasItem("FIELD_REQUIRED")))
                .andExpect(jsonPath("$.errors[?(@.field=='path')].code").value(hasItem("PAGE_PATH_INVALID")))
                .andExpect(content().string(not(containsString("Bad"))));
    }

    /** Beside a body, a header violation is still named after the header. */
    @Test
    void aHeaderBesideABodyIsNamedAfterTheHeader() throws Exception {
        mvc.perform(post("/api/test-probe/keyed-body")
                        .with(Users.anyUser())
                        .header("Idempotency-Key", " padded")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"shop\",\"path\":\"/a\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors.length()").value(1))
                .andExpect(jsonPath("$.errors[0].field").value("Idempotency-Key"))
                .andExpect(jsonPath("$.errors[0].code").value("IDEMPOTENCY_KEY_INVALID"));
        mvc.perform(post("/api/test-probe/keyed-body")
                        .with(Users.anyUser())
                        .header("Idempotency-Key", " padded")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"   \",\"path\":\"/a\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors.length()").value(2))
                .andExpect(jsonPath("$.errors[?(@.field=='Idempotency-Key')].code")
                        .value(hasItem("IDEMPOTENCY_KEY_INVALID")))
                .andExpect(jsonPath("$.errors[?(@.field=='name')].code").value(hasItem("FIELD_REQUIRED")));
    }

    @Test
    void badUuidInPathIsUuidInvalid() throws Exception {
        mvc.perform(get("/api/test-probe/items/not-a-uuid").with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("id"))
                .andExpect(jsonPath("$.errors[0].code").value("UUID_INVALID"));
    }

    @Test
    void webBlockExceptionsUseTheirCategoryStatus() throws Exception {
        mvc.perform(get("/api/test-probe/conflict").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("CONCURRENT_UPDATE"))
                .andExpect(jsonPath("$.title").value(ErrorCode.CONCURRENT_UPDATE.message()))
                .andExpect(jsonPath("$.status").value(409));
    }

    @Test
    void unknownRouteIs404() throws Exception {
        mvc.perform(get("/api/nothing-here").with(Users.anyUser()))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("ROUTE_NOT_FOUND"));
    }

    @Test
    void wrongMethodIs405WithAllow() throws Exception {
        mvc.perform(delete("/api/test-probe").with(Users.anyUser()))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(header().exists("Allow"))
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:method-not-allowed"))
                .andExpect(jsonPath("$.code").value("METHOD_NOT_ALLOWED"));
    }

    @Test
    void wrongContentTypeIs415() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.TEXT_PLAIN)
                        .content("x"))
                .andExpect(status().isUnsupportedMediaType())
                .andExpect(jsonPath("$.code").value("UNSUPPORTED_MEDIA_TYPE"));
    }

    @Test
    void internalErrorsLeakNothing() throws Exception {
        mvc.perform(get("/api/test-probe/boom").with(Users.anyUser()))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"))
                .andExpect(jsonPath("$.title").value("An unexpected error occurred."))
                .andExpect(content().string(not(containsString("secret-internal-detail"))))
                .andExpect(content().string(not(containsString("IllegalStateException"))));
    }

    @Test
    void registeredConstraintViolationsUseTheirCode() throws Exception {
        mvc.perform(get("/api/test-probe/duplicate").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("CONCURRENT_UPDATE"))
                .andExpect(content().string(not(containsString("duplicate key"))));
    }

    @Test
    void unregisteredConstraintViolationsAreInternal() throws Exception {
        mvc.perform(get("/api/test-probe/unmapped").with(Users.anyUser()))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"));
    }

    @Test
    void optimisticLockFailuresAreConcurrentUpdates() throws Exception {
        mvc.perform(get("/api/test-probe/stale").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("CONCURRENT_UPDATE"));
    }

    @Test
    void anAccessDenialRaisedInsideMvcAnswers403() throws Exception {
        mvc.perform(get("/api/test-probe/denied").with(Users.anyUser()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("ACCESS_DENIED"))
                .andExpect(content().string(not(containsString("secret-denial-detail"))));
    }

    @Test
    void everyErrorCarriesTheResponseRequestId() throws Exception {
        MvcResult result = mvc.perform(
                        get("/api/test-probe/conflict").with(Users.anyUser()).header("X-Request-Id", "trace-me"))
                .andReturn();
        assertThat(result.getResponse().getHeader("X-Request-Id")).isEqualTo("trace-me");
        assertThat(JsonPath.<String>read(result.getResponse().getContentAsString(), "$.requestId"))
                .isEqualTo("trace-me");
    }

    @Test
    void unacceptableResponseFormatIs406() throws Exception {
        mvc.perform(get("/api/test-probe").with(Users.anyUser()).accept(MediaType.APPLICATION_XML))
                .andExpect(status().isNotAcceptable())
                .andExpect(header().string("Content-Type", "application/problem+json"))
                .andExpect(jsonPath("$.code").value("NOT_ACCEPTABLE"));
    }

    @Test
    void serverSideFrameworkErrorsAreInternalAndLeakNothing() throws Exception {
        mvc.perform(get("/api/test-probe/unavailable").with(Users.anyUser()))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"))
                .andExpect(content().string(not(containsString("Service Unavailable"))));
    }

    @Test
    void dependencyFailuresAdviseWhenToRetry() throws Exception {
        mvc.perform(get("/api/test-probe/dependency").with(Users.anyUser()))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().string("Retry-After", "5"))
                .andExpect(jsonPath("$.code").value("DEPENDENCY_UNAVAILABLE"));
    }

    @Test
    void otherErrorsCarryNoRetryAfter() throws Exception {
        mvc.perform(get("/api/test-probe/conflict").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(header().doesNotExist("Retry-After"));
    }

    @Test
    void otherClientFrameworkErrorsAreMalformed() throws Exception {
        mvc.perform(get("/api/test-probe/throttled").with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
    }

    // Problem Details (spec section 6.3)

    @Test
    void anErrorIsAProblemDetailsBodyWithEveryMember() throws Exception {
        mvc.perform(get("/api/test-probe/conflict").with(Users.anyUser()).header("X-Request-Id", "req-42"))
                .andExpect(status().isConflict())
                .andExpect(header().string("Content-Type", "application/problem+json"))
                .andExpect(header().string("X-Request-Id", "req-42"))
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:concurrent-update"))
                .andExpect(jsonPath("$.title").value(ErrorCode.CONCURRENT_UPDATE.message()))
                .andExpect(jsonPath("$.status").value(409))
                .andExpect(jsonPath("$.instance").value("/api/test-probe/conflict"))
                .andExpect(jsonPath("$.code").value("CONCURRENT_UPDATE"))
                .andExpect(jsonPath("$.requestId").value("req-42"))
                .andExpect(jsonPath("$.errors").isArray())
                .andExpect(jsonPath("$.errors").isEmpty())
                .andExpect(content().string(not(containsString("\"detail\""))))
                .andExpect(content().string(not(containsString("\"traceId\""))))
                .andExpect(content().string(not(containsString("\"success\""))))
                .andExpect(content().string(not(containsString("\"meta\""))));
    }

    @Test
    void fieldErrorsAreListedUnderErrors() throws Exception {
        mvc.perform(post("/api/test-probe")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"ok\",\"path\":\"Bad\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:validation-failed"))
                .andExpect(jsonPath("$.errors[0].field").value("path"))
                .andExpect(jsonPath("$.errors[0].code").value("PAGE_PATH_INVALID"))
                .andExpect(jsonPath("$.errors[0].message").value(ErrorCode.PAGE_PATH_INVALID.message()));
    }

    @Test
    void aClientThatAcceptsOnlyJsonGetsTheSameBodyAsJson() throws Exception {
        mvc.perform(get("/api/test-probe/conflict").with(Users.anyUser()).accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isConflict())
                .andExpect(header().string("Content-Type", "application/json"))
                .andExpect(jsonPath("$.type").value("urn:webblock:problem:concurrent-update"))
                .andExpect(jsonPath("$.code").value("CONCURRENT_UPDATE"));
    }

    @Test
    void aClientThatAllowsProblemJsonGetsIt() throws Exception {
        mvc.perform(get("/api/test-probe/conflict")
                        .with(Users.anyUser())
                        .header("Accept", "application/json, application/problem+json"))
                .andExpect(header().string("Content-Type", "application/problem+json"));
        mvc.perform(get("/api/test-probe/conflict").with(Users.anyUser()).header("Accept", "*/*"))
                .andExpect(header().string("Content-Type", "application/problem+json"));
    }

    /** Review Focus 4. */
    @Test
    void theInstanceIsThePathWithoutTheQuery() throws Exception {
        mvc.perform(get("/api/test-probe/items/not-a-uuid")
                        .queryParam("note", "customer-text")
                        .with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.instance").value("/api/test-probe/items/not-a-uuid"))
                .andExpect(content().string(not(containsString("customer-text"))));
    }

    /** Review Focus 3. */
    @Test
    void aFrameworkProblemNeverLeaksItsDetail() throws Exception {
        mvc.perform(get("/api/test-probe/detailed").with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"))
                .andExpect(content().string(not(containsString("\"detail\""))))
                .andExpect(content().string(not(containsString("secret-framework-detail"))));
    }

    /** Review Focus 5. */
    @Test
    void a401RaisedInsideMvcCarriesTheBearerChallenge() throws Exception {
        mvc.perform(get("/api/test-probe/unauthenticated").with(Users.anyUser()))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("WWW-Authenticate", "Bearer"))
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"))
                .andExpect(content().string(not(containsString("secret-auth-detail"))));
    }

    private void expectMalformed(String body) throws Exception {
        mvc.perform(post("/api/test-probe/scalars")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"));
    }

    @Test
    void validScalarsAreAccepted() throws Exception {
        mvc.perform(post("/api/test-probe/scalars")
                        .with(Users.anyUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"count\":1,\"flag\":true,\"ratio\":1.5}"))
                .andExpect(status().isOk());
    }

    @Test
    void stringIsNotCoercedIntoAnInteger() throws Exception {
        expectMalformed("{\"count\":\"1\",\"flag\":true,\"ratio\":1.5}");
    }

    @Test
    void floatIsNotCoercedIntoAnInteger() throws Exception {
        expectMalformed("{\"count\":1.5,\"flag\":true,\"ratio\":1.5}");
    }

    @Test
    void stringIsNotCoercedIntoABoolean() throws Exception {
        expectMalformed("{\"count\":1,\"flag\":\"true\",\"ratio\":1.5}");
    }

    @Test
    void integerIsNotCoercedIntoABoolean() throws Exception {
        expectMalformed("{\"count\":1,\"flag\":1,\"ratio\":1.5}");
    }

    @Test
    void stringIsNotCoercedIntoAFloat() throws Exception {
        expectMalformed("{\"count\":1,\"flag\":true,\"ratio\":\"1.5\"}");
    }

    @Test
    void nullIsNotAcceptedForAPrimitive() throws Exception {
        expectMalformed("{\"count\":null,\"flag\":true,\"ratio\":1.5}");
    }
}
