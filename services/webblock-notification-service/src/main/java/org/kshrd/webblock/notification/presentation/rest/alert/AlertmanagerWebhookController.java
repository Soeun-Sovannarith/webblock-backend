package org.kshrd.webblock.notification.presentation.rest.alert;

import io.swagger.v3.oas.annotations.Hidden;
import jakarta.validation.Valid;
import org.kshrd.webblock.notification.application.alert.handler.HandleAlertmanagerWebhookHandler;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Alertmanager webhook receiver. Internal: not under /api/notification/v1, so neither the gateway nor Istio exposes it;
 * caller is checked by a shared-secret token (AlertmanagerTokenFilter), not a Keycloak JWT.
 * Errors are answered here instead of through /error: Alertmanager retries 5xx but not 4xx, so a delivery failure must
 * come back as 503 (Knock drops repeats of what was already sent) and a bad body as 400.
 */
@Hidden
@RestController
@RequestMapping("/internal/v1/alertmanager")
public class AlertmanagerWebhookController {

    private static final Logger log = LoggerFactory.getLogger(AlertmanagerWebhookController.class);

    private final HandleAlertmanagerWebhookHandler handler;

    public AlertmanagerWebhookController(HandleAlertmanagerWebhookHandler handler) {
        this.handler = handler;
    }

    @PostMapping("/alerts")
    public ResponseEntity<Void> receive(@Valid @RequestBody AlertmanagerWebhookRequest request) {
        handler.handle(request);
        return ResponseEntity.noContent().build();
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, HttpMessageNotReadableException.class})
    ResponseEntity<Void> badRequest(Exception ex) {
        log.warn("Alertmanager webhook body rejected: {}", ex.getClass().getSimpleName());
        return ResponseEntity.badRequest().build();
    }

    @ExceptionHandler(RuntimeException.class)
    ResponseEntity<Void> deliveryFailed(RuntimeException ex) {
        log.error("Alert delivery failed; Alertmanager will retry", ex);
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).build();
    }
}
