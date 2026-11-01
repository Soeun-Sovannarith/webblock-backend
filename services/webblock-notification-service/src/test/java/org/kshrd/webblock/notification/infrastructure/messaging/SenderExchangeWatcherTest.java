package org.kshrd.webblock.notification.infrastructure.messaging;

import com.rabbitmq.client.ShutdownSignalException;
import com.rabbitmq.client.impl.AMQImpl;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.infrastructure.messaging.SenderExchangeWatcher.ExchangeState;
import org.springframework.amqp.AmqpConnectException;
import org.springframework.amqp.AmqpIOException;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;

class SenderExchangeWatcherTest {

    /** A broker whose exchanges the test controls. */
    private static final class FakeBroker implements SenderExchangeWatcher.Broker {
        final Map<String, ExchangeState> states = new HashMap<>();
        final AtomicInteger topologyDeclared = new AtomicInteger();
        RuntimeException failure;

        FakeBroker(ExchangeState all) {
            SenderExchangeWatcher.SENDER_EXCHANGES.forEach(e -> states.put(e, all));
        }

        @Override
        public ExchangeState exchange(String name) {
            if (failure != null) {
                throw failure;
            }
            return states.get(name);
        }

        @Override
        public void declareOwnTopology() {
            topologyDeclared.incrementAndGet();
        }
    }

    private final SimpleMeterRegistry registry = new SimpleMeterRegistry();

    private double missing(String exchange) {
        return registry.get(SenderExchangeWatcher.MISSING_GAUGE).tag("exchange", exchange).gauge().value();
    }

    @Test
    @DisplayName("bindsOnceAtTheFirstCheckWhenEveryExchangeExistsThenLeavesTheBrokerAlone")
    void bindsOnceWhenEverythingExists() {
        FakeBroker broker = new FakeBroker(ExchangeState.PRESENT);
        SenderExchangeWatcher watcher = new SenderExchangeWatcher(broker, registry);

        watcher.check();
        watcher.check();
        watcher.check();

        assertThat(broker.topologyDeclared).hasValue(1);
        SenderExchangeWatcher.SENDER_EXCHANGES.forEach(e -> assertThat(missing(e)).isZero());
    }

    @Test
    @DisplayName("showsAMissingExchangeInTheGaugeAndDoesNotBindUntilItExists")
    void missingExchangeIsShown() {
        FakeBroker broker = new FakeBroker(ExchangeState.PRESENT);
        broker.states.put(RabbitMQConfig.PROJECT_EXCHANGE, ExchangeState.MISSING);
        SenderExchangeWatcher watcher = new SenderExchangeWatcher(broker, registry);

        watcher.check();
        watcher.check();

        assertThat(missing(RabbitMQConfig.PROJECT_EXCHANGE)).isEqualTo(1);
        assertThat(missing(RabbitMQConfig.PAYMENT_EXCHANGE)).isZero();
        assertThat(broker.topologyDeclared).hasValue(0);
    }

    @Test
    @DisplayName("bindsExactlyOnceAsSoonAsTheMissingExchangeAppearsAndClearsTheGauge")
    void bindsWhenTheExchangeAppears() {
        FakeBroker broker = new FakeBroker(ExchangeState.MISSING);
        SenderExchangeWatcher watcher = new SenderExchangeWatcher(broker, registry);
        watcher.check();
        assertThat(broker.topologyDeclared).hasValue(0);

        SenderExchangeWatcher.SENDER_EXCHANGES.forEach(e -> broker.states.put(e, ExchangeState.PRESENT));
        watcher.check();
        watcher.check();

        assertThat(broker.topologyDeclared).hasValue(1);
        SenderExchangeWatcher.SENDER_EXCHANGES.forEach(e -> assertThat(missing(e)).isZero());
    }

    @Test
    @DisplayName("bindsAgainWhenAnExchangeDisappearsAndComesBack")
    void bindsAgainAfterAnExchangeWasLostAndRecreated() {
        FakeBroker broker = new FakeBroker(ExchangeState.PRESENT);
        SenderExchangeWatcher watcher = new SenderExchangeWatcher(broker, registry);
        watcher.check();
        assertThat(broker.topologyDeclared).hasValue(1);

        broker.states.put(RabbitMQConfig.PAYMENT_EXCHANGE, ExchangeState.MISSING);
        watcher.check();
        broker.states.put(RabbitMQConfig.PAYMENT_EXCHANGE, ExchangeState.PRESENT);
        watcher.check();

        assertThat(broker.topologyDeclared).hasValue(2);
    }

    @Test
    @DisplayName("changesNothingWhileTheBrokerCannotBeReached")
    void unknownStateChangesNothing() {
        FakeBroker broker = new FakeBroker(ExchangeState.UNKNOWN);
        SenderExchangeWatcher watcher = new SenderExchangeWatcher(broker, registry);

        watcher.check();

        assertThat(broker.topologyDeclared).hasValue(0);
        SenderExchangeWatcher.SENDER_EXCHANGES.forEach(e -> assertThat(missing(e)).isZero());

        SenderExchangeWatcher.SENDER_EXCHANGES.forEach(e -> broker.states.put(e, ExchangeState.PRESENT));
        watcher.check();
        assertThat(broker.topologyDeclared).hasValue(1);
    }

    @Test
    @DisplayName("neverThrowsSoTheScheduleKeepsRunning")
    void neverThrows() {
        FakeBroker broker = new FakeBroker(ExchangeState.PRESENT);
        broker.failure = new IllegalStateException("broker exploded");
        SenderExchangeWatcher watcher = new SenderExchangeWatcher(broker, registry);

        assertThatCode(watcher::check).doesNotThrowAnyException();
    }

    // ---- reading the broker's answer ----------------------------------------------------------------------

    @Test
    @DisplayName("readsReplyCode404OnTheChannelAsAMissingExchange")
    void notFoundIsMissing() {
        var close = new AMQImpl.Channel.Close(404, "NOT_FOUND - no exchange 'project.events'", 40, 10);
        var signal = new ShutdownSignalException(false, false, close, null);

        assertThat(SenderExchangeWatcher.classify(new AmqpIOException(new IOException("channel error", signal)))).isEqualTo(ExchangeState.MISSING);
    }

    @Test
    @DisplayName("doesNotCallAnExchangeMissingWhenTheAnswerIsAnythingElse")
    void otherAnswersAreUnknown() {
        var accessRefused = new ShutdownSignalException(false, false, new AMQImpl.Channel.Close(403, "ACCESS_REFUSED", 40, 10), null);

        assertThat(SenderExchangeWatcher.classify(new AmqpIOException(new IOException("channel error", accessRefused)))).isEqualTo(ExchangeState.UNKNOWN);
        assertThat(SenderExchangeWatcher.classify(new AmqpConnectException(new IOException("refused")))).isEqualTo(ExchangeState.UNKNOWN);
        assertThat(SenderExchangeWatcher.classify(null)).isEqualTo(ExchangeState.UNKNOWN);
    }
}
