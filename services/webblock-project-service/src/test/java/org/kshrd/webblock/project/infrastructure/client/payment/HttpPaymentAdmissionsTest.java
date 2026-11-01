package org.kshrd.webblock.project.infrastructure.client.payment;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.absent;
import static com.github.tomakehurst.wiremock.client.WireMock.delete;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.equalToJson;
import static com.github.tomakehurst.wiremock.client.WireMock.okJson;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import com.github.tomakehurst.wiremock.http.Fault;
import com.github.tomakehurst.wiremock.stubbing.Scenario;
import java.net.URI;
import java.time.Duration;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.application.payment.model.AdmissionDecision;
import org.kshrd.webblock.project.application.payment.model.AdmissionRefusal;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmissionOutcome;
import org.kshrd.webblock.project.application.payment.model.WebsiteAdmission;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.infrastructure.config.payment.PaymentProperties;
import org.slf4j.MDC;
import org.springframework.security.authentication.TestingAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

class HttpPaymentAdmissionsTest {

    static final String ADMISSIONS = "/internal/v1/website-admissions";
    static final UUID CLIENT = UUID.fromString("0199e0a2-0000-7000-8000-0000000000c1");
    static final UUID PROJECT = UUID.fromString("0199e0a2-0000-7000-8000-0000000000d1");

    WireMockServer payment;
    HttpPaymentAdmissions client;

    @BeforeEach
    void start() {
        payment = new WireMockServer(WireMockConfiguration.options().dynamicPort());
        payment.start();
        client = new HttpPaymentAdmissions(
                new PaymentProperties("http", URI.create(payment.baseUrl()), Duration.ofMillis(500), ""));
    }

    @AfterEach
    void stop() {
        payment.stop();
        MDC.clear();
    }

    private static String envelope(String data) {
        return "{\"success\":true,\"data\":" + data + ",\"error\":null,\"meta\":{\"requestId\":\"r\",\"status\":200}}";
    }

    private static Throwable code(ErrorCode expected, Throwable e) {
        assertThat(((WebBlockException) e).errorCode()).isEqualTo(expected);
        return e;
    }

    @Test
    void anAdmissionIsReadAndUnknownFieldsAreIgnored() {
        MDC.put("requestId", "req-1");
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .withHeader("Authorization", absent())
                .withHeader("X-Request-Id", equalTo("req-1"))
                .withRequestBody(equalToJson("{\"clientUserId\":\"" + CLIENT + "\",\"projectId\":\"" + PROJECT + "\"}"))
                .willReturn(okJson(envelope("""
                        {"decision":"ADMITTED","projectId":"%s","planCode":"trial","websitesUsed":1,"websitesLimit":1,
                         "addedInALaterV1":{"x":1}}""".formatted(PROJECT)))));
        WebsiteAdmission admission = client.admitWebsite(CLIENT, PROJECT);
        assertThat(admission).isEqualTo(new WebsiteAdmission(AdmissionDecision.ADMITTED, null));
    }

    @Test
    void aRefusalIsANormalAnswer() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS)).willReturn(okJson(envelope("""
                {"decision":"REFUSED","reason":"WEBSITE_LIMIT_REACHED","planCode":"pro","websitesUsed":3,"websitesLimit":3}"""))));
        WebsiteAdmission admission = client.admitWebsite(CLIENT, PROJECT);
        assertThat(admission)
                .isEqualTo(new WebsiteAdmission(AdmissionDecision.REFUSED, AdmissionRefusal.WEBSITE_LIMIT_REACHED));
    }

    @Test
    void aReasonAddedLaterIsOther() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .willReturn(okJson(envelope("{\"decision\":\"REFUSED\",\"reason\":\"ADDED_IN_A_LATER_V1\"}"))));
        assertThat(client.admitWebsite(CLIENT, PROJECT).reason()).isEqualTo(AdmissionRefusal.OTHER);
    }

    @Test
    void aSlowAnswerFailsClosedAfterOneRetry() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .willReturn(okJson(envelope("{\"decision\":\"ADMITTED\"}")).withFixedDelay(1500)));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
        payment.verify(2, postRequestedFor(urlEqualTo(ADMISSIONS)));
    }

    @Test
    void anHtmlGatewayErrorFailsClosedWithoutRetry() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .willReturn(aResponse()
                        .withStatus(502)
                        .withHeader("Content-Type", "text/html")
                        .withBody("<html>bad gateway</html>")));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
        payment.verify(1, postRequestedFor(urlEqualTo(ADMISSIONS)));
    }

    @Test
    void successFalseOrAMalformedDecisionFailsClosed() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .willReturn(okJson(
                        "{\"success\":false,\"data\":null,\"error\":{\"code\":\"INTERNAL_ERROR\"},\"meta\":{}}")));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
        payment.stubFor(post(urlEqualTo(ADMISSIONS)).willReturn(okJson(envelope("{\"decision\":\"MAYBE\"}"))));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
        payment.stubFor(post(urlEqualTo(ADMISSIONS)).willReturn(okJson(envelope("{\"decision\":\"REFUSED\"}"))));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
    }

    @Test
    void anAdmissionForAnotherProjectFailsClosed() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .willReturn(
                        okJson(envelope("{\"decision\":\"ADMITTED\",\"projectId\":\"" + UUID.randomUUID() + "\"}"))));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
    }

    @Test
    void anIdempotencyConflictIsAProjectIdConflict() {
        payment.stubFor(
                post(urlEqualTo(ADMISSIONS))
                        .willReturn(
                                aResponse()
                                        .withStatus(409)
                                        .withHeader("Content-Type", "application/json")
                                        .withBody(
                                                "{\"success\":false,\"data\":null,\"error\":{\"code\":\"IDEMPOTENCY_CONFLICT\"},\"meta\":{}}")));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PROJECT_ID_CONFLICT, e));
    }

    /** A reset is an I/O failure, and both calls are idempotent: tried once more, at once. */
    @Test
    void aConnectionResetAtPaymentIsTriedOnceMore() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .inScenario("reset")
                .whenScenarioStateIs(Scenario.STARTED)
                .willSetStateTo("recovered")
                .willReturn(aResponse().withFault(Fault.CONNECTION_RESET_BY_PEER)));
        payment.stubFor(post(urlEqualTo(ADMISSIONS))
                .inScenario("reset")
                .whenScenarioStateIs("recovered")
                .willReturn(okJson(envelope("{\"decision\":\"ADMITTED\"}"))));

        assertThat(client.admitWebsite(CLIENT, PROJECT).decision()).isEqualTo(AdmissionDecision.ADMITTED);
        payment.verify(2, postRequestedFor(urlEqualTo(ADMISSIONS)));
    }

    @Test
    void aConnectionThatKeepsResettingFailsClosedAfterTwoAttempts() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS)).willReturn(aResponse().withFault(Fault.CONNECTION_RESET_BY_PEER)));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
        payment.verify(2, postRequestedFor(urlEqualTo(ADMISSIONS)));
    }

    /** Mesh identity, no bearer (authentication spec 5.1): not even the calling user's token is passed on. */
    @Test
    void noCredentialHeaderIsSentEvenWhenAUserIsSignedIn() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS)).willReturn(okJson(envelope("{\"decision\":\"ADMITTED\"}"))));
        payment.stubFor(delete(urlEqualTo(ADMISSIONS + "/" + PROJECT))
                .willReturn(aResponse().withStatus(204)));
        try {
            SecurityContextHolder.getContext().setAuthentication(new TestingAuthenticationToken("alice", "a-token"));
            client.admitWebsite(CLIENT, PROJECT);
            client.releaseWebsiteAdmission(PROJECT);
        } finally {
            SecurityContextHolder.clearContext();
        }
        assertThat(payment.getAllServeEvents()).hasSize(2).allSatisfy(event -> {
            assertThat(event.getRequest().containsHeader("Authorization")).isFalse();
            assertThat(event.getRequest().containsHeader("Cookie")).isFalse();
            assertThat(event.getRequest().containsHeader("Proxy-Authorization")).isFalse();
        });
    }

    @Test
    void releaseMapsTheContractsThreeAnswers() {
        String path = ADMISSIONS + "/" + PROJECT;
        payment.stubFor(delete(urlEqualTo(path)).willReturn(aResponse().withStatus(204)));
        assertThat(client.releaseWebsiteAdmission(PROJECT)).isEqualTo(AdmissionReleaseOutcome.RELEASED);
        payment.stubFor(
                delete(urlEqualTo(path))
                        .willReturn(
                                aResponse()
                                        .withStatus(404)
                                        .withHeader("Content-Type", "application/json")
                                        .withBody(
                                                "{\"success\":false,\"data\":null,\"error\":{\"code\":\"NOT_ADMITTED\"},\"meta\":{}}")));
        assertThat(client.releaseWebsiteAdmission(PROJECT)).isEqualTo(AdmissionReleaseOutcome.NOT_ADMITTED);
        payment.stubFor(
                delete(urlEqualTo(path))
                        .willReturn(
                                aResponse()
                                        .withStatus(409)
                                        .withHeader("Content-Type", "application/json")
                                        .withBody(
                                                "{\"success\":false,\"data\":null,\"error\":{\"code\":\"PROJECT_IN_USE\"},\"meta\":{}}")));
        assertThat(client.releaseWebsiteAdmission(PROJECT)).isEqualTo(AdmissionReleaseOutcome.PROJECT_IN_USE);
        payment.stubFor(delete(urlEqualTo(path)).willReturn(aResponse().withStatus(500)));
        assertThatThrownBy(() -> client.releaseWebsiteAdmission(PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
    }

    @Test
    void aRedirectIsNotFollowed() {
        WireMockServer other =
                new WireMockServer(WireMockConfiguration.options().dynamicPort());
        other.start();
        try {
            other.stubFor(post(urlEqualTo("/elsewhere")).willReturn(okJson(envelope("{\"decision\":\"ADMITTED\"}"))));
            payment.stubFor(post(urlEqualTo(ADMISSIONS))
                    .willReturn(aResponse().withStatus(307).withHeader("Location", other.baseUrl() + "/elsewhere")));
            assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                    .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
            other.verify(0, postRequestedFor(urlEqualTo("/elsewhere")));
            assertThat(other.getAllServeEvents()).isEmpty();
        } finally {
            other.stop();
        }
    }

    @Test
    void anEnvelopeWithoutADecisionFailsClosed() {
        payment.stubFor(post(urlEqualTo(ADMISSIONS)).willReturn(okJson(envelope("{\"planCode\":\"trial\"}"))));
        assertThatThrownBy(() -> client.admitWebsite(CLIENT, PROJECT))
                .satisfies(e -> code(ErrorCode.PAYMENT_UNAVAILABLE, e));
    }

    static final String GENERATIONS = "/internal/v1/generation-admissions";
    static final UUID REQUEST = UUID.fromString("0199e0a2-0000-7000-8000-0000000000e1");

    private GenerationAdmission generation() {
        return client.admitGeneration(CLIENT, PROJECT, REQUEST);
    }

    @Test
    void aGenerationAdmissionIsReadAndTheBodyIsTheContractsThreeIds() {
        MDC.put("requestId", "req-2");
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .withHeader("Authorization", absent())
                .withHeader("X-Request-Id", equalTo("req-2"))
                .withRequestBody(equalToJson("{\"clientUserId\":\"" + CLIENT + "\",\"projectId\":\"" + PROJECT
                        + "\",\"generationRequestId\":\"" + REQUEST + "\"}"))
                .willReturn(okJson(envelope(
                        "{\"decision\":\"ADMITTED\",\"attemptsUsed\":2,\"attemptsLimit\":3,\"later\":\"x\"}"))));
        GenerationAdmission admission = generation();
        assertThat(admission.outcome()).isEqualTo(GenerationAdmissionOutcome.ADMITTED);
        assertThat(admission.reason()).isNull();
        payment.verify(1, postRequestedFor(urlEqualTo(GENERATIONS)).withoutHeader("Authorization"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"TRIAL_GENERATION_LIMIT_REACHED", "PLAN_PAUSED"})
    void aGenerationRefusalCarriesTheRawReason(String reason) {
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .willReturn(okJson(envelope("{\"decision\":\"REFUSED\",\"reason\":\"" + reason + "\"}"))));
        assertThat(generation()).isEqualTo(GenerationAdmission.refused(reason));
    }

    @Test
    void aGenerationRefusalWithoutAReasonIsAnError() {
        payment.stubFor(post(urlEqualTo(GENERATIONS)).willReturn(okJson(envelope("{\"decision\":\"REFUSED\"}"))));
        assertThat(generation().outcome()).isEqualTo(GenerationAdmissionOutcome.ERROR);
    }

    @ParameterizedTest
    @ValueSource(ints = {500, 502, 503, 504})
    void aServerErrorIsUnavailableForAGeneration(int status) {
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .willReturn(aResponse()
                        .withStatus(status)
                        .withHeader("Content-Type", "text/html")
                        .withBody("<html>oops</html>")));
        assertThat(generation()).isEqualTo(GenerationAdmission.UNAVAILABLE);
    }

    @Test
    void aTimeoutIsUnavailableForAGeneration() {
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .willReturn(okJson(envelope("{\"decision\":\"ADMITTED\"}")).withFixedDelay(700)));
        assertThat(generation()).isEqualTo(GenerationAdmission.UNAVAILABLE);
        payment.verify(2, postRequestedFor(urlEqualTo(GENERATIONS)));
    }

    @Test
    void aConnectionResetIsUnavailableForAGeneration() {
        payment.stubFor(
                post(urlEqualTo(GENERATIONS)).willReturn(aResponse().withFault(Fault.CONNECTION_RESET_BY_PEER)));
        assertThat(generation()).isEqualTo(GenerationAdmission.UNAVAILABLE);
    }

    static Stream<Arguments> otherAnswers() {
        String conflict = "{\"success\":false,\"data\":null,\"error\":{\"code\":\"IDEMPOTENCY_CONFLICT\"},\"meta\":{}}";
        String refused = "{\"success\":false,\"data\":null,\"error\":{\"code\":\"X\"},\"meta\":{}}";
        return Stream.of(
                Arguments.of(200, "{\"success\":false,\"data\":null,\"error\":{\"code\":\"X\"},\"meta\":{}}"),
                Arguments.of(200, "not json"),
                Arguments.of(200, "{\"success\":true,\"data\":\"ADMITTED\",\"error\":null}"),
                Arguments.of(200, "{\"success\":true,\"data\":{\"decision\":\"MAYBE\"},\"error\":null}"),
                Arguments.of(400, refused),
                Arguments.of(401, refused),
                Arguments.of(403, refused),
                Arguments.of(404, refused),
                Arguments.of(409, conflict));
    }

    @ParameterizedTest
    @MethodSource("otherAnswers")
    void anyOtherAnswerIsAnErrorForAGeneration(int status, String body) {
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .willReturn(aResponse()
                        .withStatus(status)
                        .withHeader("Content-Type", "application/json")
                        .withBody(body)));
        assertThat(generation()).isEqualTo(GenerationAdmission.ERROR);
    }
}
