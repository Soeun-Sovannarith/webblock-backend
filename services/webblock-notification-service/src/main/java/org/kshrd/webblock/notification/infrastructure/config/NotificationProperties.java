package org.kshrd.webblock.notification.infrastructure.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;
import java.util.List;

/**
 * Settings under "webblock.notification". Workflow keys must exist in the Knock dashboard.
 * Secrets and personal data come from env vars and are never printed (see toString of Admin and Knock).
 */
@ConfigurationProperties("webblock.notification")
public record NotificationProperties(
        @DefaultValue Workflows workflows,
        List<Admin> admins,
        @DefaultValue Stub stub,
        @DefaultValue Knock knock,
        @DefaultValue Alertmanager alertmanager) {

    public NotificationProperties {
        admins = admins == null ? List.of() : List.copyOf(admins);
    }

    /** Knock workflow key for each notification. The channels (web, mobile, email) are set inside each Knock workflow. */
    public record Workflows(
            @DefaultValue("generation-completed") String generationCompleted,
            @DefaultValue("deployment-succeeded") String deploymentSucceeded,
            @DefaultValue("deployment-failed-owner") String deploymentFailedOwner,
            @DefaultValue("deployment-failed-admin") String deploymentFailedAdmin,
            @DefaultValue("subscription-expired-admin") String subscriptionExpiredAdmin,
            @DefaultValue("subscription-activated-admin") String subscriptionActivatedAdmin,
            // Sent from Alertmanager alerts (see HandleAlertmanagerWebhookHandler)
            @DefaultValue("generation-stuck-admin") String generationStuckAdmin,
            @DefaultValue("website-crashed-owner") String websiteCrashedOwner,
            @DefaultValue("website-crashed-admin") String websiteCrashedAdmin,
            @DefaultValue("resource-limit-exceeded") String resourceLimitExceededOwner,
            @DefaultValue("resource-limit-exceeded-admin") String resourceLimitExceededAdmin,
            @DefaultValue("platform-service-down-admin") String platformServiceDownAdmin,
            // Sent from payment.payment_due.v1
            @DefaultValue("payment-due-owner") String paymentDueOwner) {
    }

    /** Admin who receives platform notifications. Temporary source until the Keycloak admin lookup exists. */
    public record Admin(String id, String name, String email) {
        @Override
        public String toString() {
            return "Admin[id=" + id + "]";
        }
    }

    /** Fake owner and contact, used only with the "local-stub" profile. */
    public record Stub(
            @DefaultValue("stub-owner-1") String ownerUserId,
            @DefaultValue("Demo Project") String projectName,
            @DefaultValue("Stub Owner") String contactName,
            String contactEmail) {
        @Override
        public String toString() {
            return "Stub[ownerUserId=" + ownerUserId + "]";
        }
    }

    /** Knock user-token signing (for the web notification bell). */
    public record Knock(String signingKey, @DefaultValue("1h") Duration tokenTtl) {
        @Override
        public String toString() {
            return "Knock[tokenTtl=" + tokenTtl + "]";
        }
    }

    /** Shared secret Alertmanager sends as "Authorization: Bearer <token>". Empty = every webhook call is refused. */
    public record Alertmanager(String webhookToken) {
        @Override
        public String toString() {
            return "Alertmanager[webhookToken=***]";
        }
    }
}
