package org.kshrd.webblock.notification.presentation.rest.alert;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.application.alert.handler.HandleAlertmanagerWebhookHandler;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

// Full security chain + controller; the handler is a mock. No RabbitMQ in tests: its health check is off and the listeners
// are not started, so this test never attaches a consumer to whatever broker runs on localhost.
@SpringBootTest(properties = {"eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "management.health.rabbit.enabled=false",
        "spring.rabbitmq.listener.simple.auto-startup=false",
        "webblock.notification.alertmanager.webhook-token=test-secret"})
@AutoConfigureMockMvc
class AlertmanagerWebhookControllerTest {

    private static final String PATH = "/internal/v1/alertmanager/alerts";
    private static final String BODY = """
            {"version":"4","status":"firing","receiver":"webblock","alerts":[
              {"status":"firing","labels":{"alertname":"GenerationStuck"},
               "startsAt":"2026-10-28T07:00:00Z","fingerprint":"abc123"}]}
            """;

    @Autowired
    MockMvc mockMvc;

    @MockitoBean
    HandleAlertmanagerWebhookHandler handler;

    @Test
    @DisplayName("acceptsAlertsWithTheSharedToken")
    void acceptsWithToken() throws Exception {
        mockMvc.perform(post(PATH).header("Authorization", "Bearer test-secret")
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isNoContent());
        verify(handler).handle(any());
    }

    @Test
    @DisplayName("refusesNoTokenAWrongTokenAndAKeycloakToken")
    void refusesOtherCallers() throws Exception {
        mockMvc.perform(post(PATH).contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isUnauthorized());
        mockMvc.perform(post(PATH).header("Authorization", "Bearer wrong")
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isUnauthorized());
        // A signed-in user (even an admin) cannot post alerts
        mockMvc.perform(post(PATH).with(jwt()).contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isUnauthorized());
        verify(handler, never()).handle(any());
    }

    @Test
    @DisplayName("answers503WhenDeliveryFailsSoAlertmanagerRetries")
    void deliveryFailureIs503() throws Exception {
        doThrow(new IllegalStateException("Knock down")).when(handler).handle(any());

        mockMvc.perform(post(PATH).header("Authorization", "Bearer test-secret")
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isServiceUnavailable());
    }

    @Test
    @DisplayName("answers400ForABadBodySoAlertmanagerDoesNotRetryIt")
    void badBodyIs400() throws Exception {
        mockMvc.perform(post(PATH).header("Authorization", "Bearer test-secret")
                        .contentType(MediaType.APPLICATION_JSON).content("{not json"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(post(PATH).header("Authorization", "Bearer test-secret")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"version\":\"4\"}"))
                .andExpect(status().isBadRequest());
        verify(handler, never()).handle(any());
    }
}
