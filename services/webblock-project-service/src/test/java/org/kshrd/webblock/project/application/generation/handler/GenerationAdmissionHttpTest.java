package org.kshrd.webblock.project.application.generation.handler;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.okJson;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import java.time.Clock;
import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.MutableClock;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.convention.TestBean;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The admission job's work against Payment over HTTP (decision J2-21): the real client, WireMock as Payment. The
 * prototype profile does not apply the production URL rule, so http://localhost is accepted.
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=4",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2",
            "webblock.generation.admission-window=15m"
        })
class GenerationAdmissionHttpTest {

    static final String GENERATIONS = "/internal/v1/generation-admissions";
    static final MutableClock CLOCK = new MutableClock();
    static WireMockServer payment;

    @TestBean(name = "clock", methodName = "mutableClock")
    Clock clock;

    static Clock mutableClock() {
        return CLOCK;
    }

    @BeforeAll
    static void startPayment() {
        payment = new WireMockServer(WireMockConfiguration.options().dynamicPort());
        payment.start();
    }

    @AfterAll
    static void stopPayment() {
        payment.stop();
    }

    @DynamicPropertySource
    static void paymentOverHttp(DynamicPropertyRegistry registry) {
        registry.add("webblock.payment.mode", () -> "http");
        registry.add("webblock.payment.base-url", () -> payment.baseUrl());
    }

    @Autowired
    GenerationAdmissionService admissions;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    GenerationRows rows;

    @BeforeEach
    void reset() {
        payment.resetAll();
        rows = new GenerationRows(jdbc, CLOCK);
    }

    private static String envelope(String data) {
        return "{\"success\":true,\"data\":" + data + ",\"error\":null,\"meta\":{\"requestId\":\"r\",\"status\":200}}";
    }

    private UUID captured() {
        UUID owner = UUID.randomUUID();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Http", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        return rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
    }

    private String statusAndCode(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT status || '/' || coalesce(failure_code, '-') FROM project.generation_requests"
                        + " WHERE generation_request_id = ?",
                String.class,
                requestId);
    }

    @Test
    void a503ThenTheWindowEndStoresAdmissionUnavailable() {
        UUID requestId = captured();
        payment.stubFor(post(urlEqualTo(GENERATIONS)).willReturn(aResponse().withStatus(503)));

        assertThatThrownBy(() -> admissions.admit(requestId)).isInstanceOf(AdmissionUnavailable.class);
        assertThat(statusAndCode(requestId)).isEqualTo("captured/-");

        CLOCK.advance(Duration.ofMinutes(15));
        admissions.admit(requestId);
        assertThat(statusAndCode(requestId)).isEqualTo("failed/admission_unavailable");
    }

    @Test
    void a400StoresAdmissionErrorAtOnce() {
        UUID requestId = captured();
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .willReturn(aResponse()
                        .withStatus(400)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"success\":false,\"data\":null,\"error\":{\"code\":\"X\"},\"meta\":{}}")));

        admissions.admit(requestId);

        assertThat(statusAndCode(requestId)).isEqualTo("failed/admission_error");
    }

    @Test
    void aRefusalOverHttpStoresItsLowerCaseReason() {
        UUID requestId = captured();
        payment.stubFor(post(urlEqualTo(GENERATIONS))
                .willReturn(
                        okJson(envelope("{\"decision\":\"REFUSED\",\"reason\":\"TRIAL_GENERATION_LIMIT_REACHED\"}"))));

        admissions.admit(requestId);

        assertThat(statusAndCode(requestId)).isEqualTo("failed/trial_generation_limit_reached");
    }

    @Test
    void noAuthorizationHeaderIsSent() {
        UUID requestId = captured();
        payment.stubFor(post(urlEqualTo(GENERATIONS)).willReturn(okJson(envelope("{\"decision\":\"ADMITTED\"}"))));

        admissions.admit(requestId);

        assertThat(statusAndCode(requestId)).isEqualTo("generating/-");
        payment.verify(1, postRequestedFor(urlEqualTo(GENERATIONS)).withoutHeader("Authorization"));
    }
}
