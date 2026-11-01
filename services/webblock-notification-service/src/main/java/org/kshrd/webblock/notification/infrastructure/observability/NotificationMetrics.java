package org.kshrd.webblock.notification.infrastructure.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.kshrd.webblock.notification.infrastructure.messaging.RabbitMQConfig;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * The numbers this service reports to Prometheus (names follow observability.md: webblock_&lt;service&gt;_&lt;what&gt;).
 * Every tag has a small fixed set of values (5 queues, the configured workflow keys, 3 outcomes), so the number of series
 * stays bounded. No tag or value holds a user id, an email or a message body.
 */
@Component
public class NotificationMetrics {

    public static final String EVENTS_RECEIVED = "webblock_notification_events_received_total";
    public static final String EVENTS_DEAD_LETTERED = "webblock_notification_events_dead_lettered_total";
    public static final String DELIVERY = "webblock_notification_delivery_total";
    public static final String KNOCK_CALL = "webblock_notification_knock_call_seconds";

    public static final String SUCCESS = "success";
    public static final String TEMPORARY_FAILURE = "temporary_failure";
    public static final String PERMANENT_FAILURE = "permanent_failure";

    private static final List<String> QUEUES = List.of(RabbitMQConfig.SOURCE_PUBLISHED_QUEUE, RabbitMQConfig.PUBLISH_COMPLETED_QUEUE,
            RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE, RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE,
            RabbitMQConfig.PAYMENT_DUE_QUEUE);

    private final MeterRegistry registry;

    public NotificationMetrics(MeterRegistry registry) {
        this.registry = registry;
        // Start every queue counter at 0 so a dashboard or alert sees the series before the first event arrives
        QUEUES.forEach(queue -> {
            eventReceived(queue, 0);
            eventDeadLettered(queue, 0);
        });
    }

    /** A message was delivered to a listener of this queue (a retry inside the listener is not counted again). */
    public void eventReceived(String queue) {
        eventReceived(queue, 1);
    }

    /** Retries are used up, or the failure is permanent: the message is rejected and RabbitMQ moves it to the queue's DLQ. */
    public void eventDeadLettered(String queue) {
        eventDeadLettered(queue, 1);
    }

    /** One try to hand a notification to Knock ended with this outcome (SUCCESS, TEMPORARY_FAILURE or PERMANENT_FAILURE). */
    public void delivery(String workflow, String outcome) {
        Counter.builder(DELIVERY)
                .description("Tries to hand a notification to Knock, by workflow and outcome")
                .tag("workflow", workflow == null ? "unknown" : workflow)
                .tag("outcome", outcome)
                .register(registry)
                .increment();
    }

    public Timer.Sample startKnockCall() {
        return Timer.start(registry);
    }

    /**
     * @param step       "identify" or "trigger"
     * @param outcome    SUCCESS, TEMPORARY_FAILURE or PERMANENT_FAILURE
     * @param httpStatus the status Knock answered with, or null for success or when there was no HTTP answer
     */
    public void stopKnockCall(Timer.Sample sample, String step, String outcome, Integer httpStatus) {
        sample.stop(Timer.builder(KNOCK_CALL)
                .description("Time spent in one call to the Knock API")
                .tag("step", step)
                .tag("outcome", outcome)
                .tag("status_class", statusClass(httpStatus))
                .register(registry));
    }

    static String statusClass(Integer httpStatus) {
        if (httpStatus == null) {
            return "none";
        }
        return httpStatus >= 100 && httpStatus < 600 ? (httpStatus / 100) + "xx" : "none";
    }

    private void eventReceived(String queue, double amount) {
        Counter counter = Counter.builder(EVENTS_RECEIVED)
                .description("Messages delivered to a listener, by queue")
                .tag("queue", queue == null ? "unknown" : queue)
                .register(registry);
        if (amount > 0) {
            counter.increment(amount);
        }
    }

    private void eventDeadLettered(String queue, double amount) {
        Counter counter = Counter.builder(EVENTS_DEAD_LETTERED)
                .description("Messages rejected without requeue (moved to the queue's DLQ), by queue")
                .tag("queue", queue == null ? "unknown" : queue)
                .register(registry);
        if (amount > 0) {
            counter.increment(amount);
        }
    }
}
