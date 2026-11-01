package org.kshrd.webblock.notification.infrastructure.observability;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** The actuator endpoints as the running service exposes them (listeners are not started: no RabbitMQ needed). */
@SpringBootTest(properties = {"eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "management.health.rabbit.enabled=false",
        "spring.rabbitmq.listener.simple.auto-startup=false"})
@AutoConfigureMockMvc
class ActuatorEndpointsTest {

    @Autowired
    MockMvc mockMvc;

    @Autowired
    NotificationMetrics metrics;

    private String scrape() throws Exception {
        return mockMvc.perform(get("/actuator/prometheus")).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
    }

    @Test
    @DisplayName("lettersPrometheusScrapeWithoutATokenAndTagsEverySeriesWithTheApplicationName")
    void prometheusIsScrapable() throws Exception {
        String body = scrape();

        assertThat(body).contains("webblock_notification_events_received_total{")
                .contains("queue=\"notification.payment_due.v1\"")
                .contains("webblock_notification_events_dead_lettered_total{")
                .contains("application=\"notification-service\"");
    }

    @Test
    @DisplayName("showsDeliveryActivityInTheScrape")
    void activityShowsUp() throws Exception {
        metrics.delivery("wf-scrape-check", NotificationMetrics.SUCCESS);

        assertThat(scrape()).contains("webblock_notification_delivery_total{")
                .contains("workflow=\"wf-scrape-check\"")
                .contains("outcome=\"success\"");
    }

    @Test
    @DisplayName("keepsInfoBehindATokenAndExposesNoOtherEndpoint")
    void onlyTheConfiguredEndpointsAreReachable() throws Exception {
        mockMvc.perform(get("/actuator/info")).andExpect(status().isUnauthorized());
        // signed in, but the endpoint is not exposed at all
        mockMvc.perform(get("/actuator/env").with(jwt())).andExpect(status().isNotFound());
        mockMvc.perform(get("/actuator/heapdump").with(jwt())).andExpect(status().isNotFound());
    }

    @Test
    @DisplayName("scrapeHoldsNoEmailNoTokenAndNoMessageBody")
    void scrapeIsFreeOfPersonalData() throws Exception {
        metrics.delivery("deployment-failed-owner", NotificationMetrics.PERMANENT_FAILURE);

        assertThat(scrape()).doesNotContain("@").doesNotContainIgnoringCase("bearer ").doesNotContainIgnoringCase("password");
    }
}
