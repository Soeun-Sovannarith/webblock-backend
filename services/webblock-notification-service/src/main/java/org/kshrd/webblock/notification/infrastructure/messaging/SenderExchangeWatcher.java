package org.kshrd.webblock.notification.infrastructure.messaging;

import com.rabbitmq.client.AMQP;
import com.rabbitmq.client.ShutdownSignalException;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * The sender exchanges (project.events, provisioning.events, payment.events) belong to the sending services, so this service
 * does not declare them. The price: if one does not exist yet when we start, RabbitMQ refuses to bind our queue to it, and the
 * service keeps running with consumers attached but bound to nothing, so it would silently never receive an event
 * (shown on a real broker). This watcher makes that visible and self-healing:
 * <ul>
 *   <li>an exchange that is missing is logged once as an ERROR and shown by the gauge
 *       {@code webblock_notification_sender_exchange_missing{exchange}} = 1 (alert on it);</li>
 *   <li>once every exchange exists, our own queues and bindings are (re)declared, so no restart is needed. This also runs
 *       once at the first check, in case an exchange appeared between start-up and that check.</li>
 * </ul>
 * It only asks "does it exist?" (a passive declare, which needs no more than read permission) and never creates or changes
 * an exchange. If the broker cannot be reached nothing is changed: the listeners reconnect and redeclare by themselves.
 */
public class SenderExchangeWatcher {

    enum ExchangeState { PRESENT, MISSING, UNKNOWN }

    /** What the watcher needs from the broker; a seam so the logic can be tested without one. */
    interface Broker {
        ExchangeState exchange(String name);

        /** (Re)declares this service's own queues, dead-letter queues and bindings. */
        void declareOwnTopology();
    }

    private static final Logger log = LoggerFactory.getLogger(SenderExchangeWatcher.class);

    static final String MISSING_GAUGE = "webblock_notification_sender_exchange_missing";
    static final List<String> SENDER_EXCHANGES = List.of(
            RabbitMQConfig.PROJECT_EXCHANGE, RabbitMQConfig.PROVISIONING_EXCHANGE, RabbitMQConfig.PAYMENT_EXCHANGE);

    private final Broker broker;
    private final Map<String, AtomicInteger> missingGauges = new LinkedHashMap<>();
    private final Set<String> reportedMissing = new HashSet<>();
    private boolean bindingsMayBeMissing = true;

    public SenderExchangeWatcher(Broker broker, MeterRegistry registry) {
        this.broker = broker;
        for (String exchange : SENDER_EXCHANGES) {
            AtomicInteger missing = new AtomicInteger(0);
            missingGauges.put(exchange, missing);
            Gauge.builder(MISSING_GAUGE, missing, AtomicInteger::get)
                    .description("1 while a sender exchange does not exist, so our queues cannot be bound to it")
                    .tag("exchange", exchange)
                    .register(registry);
        }
    }

    /** One check; called on a fixed delay. Never throws, so the schedule keeps running. */
    public void check() {
        try {
            boolean anyMissing = false;
            boolean anyUnknown = false;
            for (String exchange : SENDER_EXCHANGES) {
                ExchangeState state = broker.exchange(exchange);
                switch (state) {
                    case MISSING -> {
                        anyMissing = true;
                        missingGauges.get(exchange).set(1);
                        if (reportedMissing.add(exchange)) {
                            log.error("Sender exchange [{}] does not exist, so the queues bound to it receive no events. "
                                    + "It is created by the sending service; it is checked again soon and bound as soon as it exists.", exchange);
                        }
                    }
                    case PRESENT -> {
                        missingGauges.get(exchange).set(0);
                        if (reportedMissing.remove(exchange)) {
                            log.info("Sender exchange [{}] exists now.", exchange);
                        }
                    }
                    case UNKNOWN -> anyUnknown = true;
                }
            }
            if (anyMissing) {
                bindingsMayBeMissing = true;
            } else if (!anyUnknown && bindingsMayBeMissing) {
                broker.declareOwnTopology();
                bindingsMayBeMissing = false;
                log.info("Every sender exchange exists; this service's queues and bindings are declared.");
            }
        } catch (RuntimeException ex) {
            log.warn("Sender exchange check failed ({}); it will be tried again.", ex.getClass().getSimpleName());
        }
    }

    /** RabbitMQ answers a passive declare of an unknown exchange by closing the channel with reply code 404. */
    static ExchangeState classify(Throwable failure) {
        for (Throwable t = failure; t != null; t = t.getCause()) {
            if (t instanceof ShutdownSignalException signal && signal.getReason() instanceof AMQP.Channel.Close close
                    && close.getReplyCode() == 404) {
                return ExchangeState.MISSING;
            }
        }
        return ExchangeState.UNKNOWN;
    }
}
