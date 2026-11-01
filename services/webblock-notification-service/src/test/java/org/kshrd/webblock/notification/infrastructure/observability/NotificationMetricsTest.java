package org.kshrd.webblock.notification.infrastructure.observability;

import io.micrometer.core.instrument.Timer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationMetricsTest {

    private final SimpleMeterRegistry registry = new SimpleMeterRegistry();
    private final NotificationMetrics metrics = new NotificationMetrics(registry);

    private double count(String name, String... tags) {
        return registry.get(name).tags(tags).counter().count();
    }

    @Test
    @DisplayName("startsEveryQueueCounterAtZeroSoDashboardsSeeTheSeriesBeforeTheFirstEvent")
    void queueCountersExistFromTheStart() {
        for (String queue : new String[]{RabbitMQConfig.SOURCE_PUBLISHED_QUEUE, RabbitMQConfig.PUBLISH_COMPLETED_QUEUE,
                RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE, RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE,
                RabbitMQConfig.PAYMENT_DUE_QUEUE}) {
            assertThat(count(NotificationMetrics.EVENTS_RECEIVED, "queue", queue)).isZero();
            assertThat(count(NotificationMetrics.EVENTS_DEAD_LETTERED, "queue", queue)).isZero();
        }
    }

    @Test
    @DisplayName("countsReceivedAndDeadLetteredEventsPerQueue")
    void countsPerQueue() {
        metrics.eventReceived(RabbitMQConfig.PAYMENT_DUE_QUEUE);
        metrics.eventReceived(RabbitMQConfig.PAYMENT_DUE_QUEUE);
        metrics.eventDeadLettered(RabbitMQConfig.PAYMENT_DUE_QUEUE);

        assertThat(count(NotificationMetrics.EVENTS_RECEIVED, "queue", RabbitMQConfig.PAYMENT_DUE_QUEUE)).isEqualTo(2);
        assertThat(count(NotificationMetrics.EVENTS_DEAD_LETTERED, "queue", RabbitMQConfig.PAYMENT_DUE_QUEUE)).isEqualTo(1);
        assertThat(count(NotificationMetrics.EVENTS_RECEIVED, "queue", RabbitMQConfig.SOURCE_PUBLISHED_QUEUE)).isZero();
    }

    @Test
    @DisplayName("usesUnknownWhenTheQueueOrWorkflowIsMissingInsteadOfFailing")
    void missingTagsBecomeUnknown() {
        metrics.eventReceived(null);
        metrics.delivery(null, NotificationMetrics.SUCCESS);

        assertThat(count(NotificationMetrics.EVENTS_RECEIVED, "queue", "unknown")).isEqualTo(1);
        assertThat(count(NotificationMetrics.DELIVERY, "workflow", "unknown", "outcome", "success")).isEqualTo(1);
    }

    @Test
    @DisplayName("countsDeliveryTriesByWorkflowAndOutcome")
    void countsDelivery() {
        metrics.delivery("deployment-failed-owner", NotificationMetrics.SUCCESS);
        metrics.delivery("deployment-failed-owner", NotificationMetrics.TEMPORARY_FAILURE);
        metrics.delivery("deployment-failed-owner", NotificationMetrics.TEMPORARY_FAILURE);
        metrics.delivery("deployment-failed-owner", NotificationMetrics.PERMANENT_FAILURE);

        assertThat(count(NotificationMetrics.DELIVERY, "workflow", "deployment-failed-owner", "outcome", "success")).isEqualTo(1);
        assertThat(count(NotificationMetrics.DELIVERY, "workflow", "deployment-failed-owner", "outcome", "temporary_failure")).isEqualTo(2);
        assertThat(count(NotificationMetrics.DELIVERY, "workflow", "deployment-failed-owner", "outcome", "permanent_failure")).isEqualTo(1);
    }

    @Test
    @DisplayName("timesAKnockCallWithStepOutcomeAndStatusClass")
    void timesKnockCalls() {
        Timer.Sample sample = metrics.startKnockCall();
        metrics.stopKnockCall(sample, "trigger", NotificationMetrics.PERMANENT_FAILURE, 401);

        Timer timer = registry.get(NotificationMetrics.KNOCK_CALL)
                .tags("step", "trigger", "outcome", "permanent_failure", "status_class", "4xx").timer();
        assertThat(timer.count()).isEqualTo(1);
    }

    @Test
    @DisplayName("groupsStatusCodesIntoAClassSoTheTagStaysSmall")
    void statusClass() {
        assertThat(NotificationMetrics.statusClass(null)).isEqualTo("none");
        assertThat(NotificationMetrics.statusClass(404)).isEqualTo("4xx");
        assertThat(NotificationMetrics.statusClass(429)).isEqualTo("4xx");
        assertThat(NotificationMetrics.statusClass(503)).isEqualTo("5xx");
        assertThat(NotificationMetrics.statusClass(0)).isEqualTo("none");
        assertThat(NotificationMetrics.statusClass(999)).isEqualTo("none");
    }

    @Test
    @DisplayName("neverPutsAnIdAnEmailOrAMessageBodyInAMeterTag")
    void noPersonalDataInTags() {
        metrics.delivery("payment-due-owner", NotificationMetrics.SUCCESS);
        metrics.eventReceived(RabbitMQConfig.PAYMENT_DUE_QUEUE);

        registry.getMeters().forEach(meter -> meter.getId().getTags().forEach(tag ->
                assertThat(tag.getValue()).doesNotContain("@").doesNotContainPattern("[0-9a-f]{8}-[0-9a-f]{4}-")));
    }
}
