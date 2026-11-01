package org.kshrd.webblock.notification.infrastructure.messaging;

import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.annotation.DirtiesContext;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The application as it runs for real: listeners start automatically, so the sender-exchange watcher is wired.
 * The broker is deliberately a dead local port (nothing listens on port 1), so this test can never touch a real RabbitMQ;
 * the listeners just keep retrying to connect in the background and the context closes at the end.
 */
@SpringBootTest(properties = {
        "eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "knock.api-key=dummy-key-not-a-real-credential",
        "spring.rabbitmq.host=localhost",
        "spring.rabbitmq.port=1",
        "management.health.rabbit.enabled=false"})
@DirtiesContext
class SenderExchangeWatcherWiringTest {

    @Autowired
    SenderExchangeWatcher watcher;

    @Autowired
    MeterRegistry registry;

    @Test
    @DisplayName("startsTheWatcherWhenListenersStartAutomaticallyAndNoBrokerChangesAreMadeWithoutABroker")
    void watcherIsWired() {
        assertThat(watcher).isNotNull();
        for (String exchange : SenderExchangeWatcher.SENDER_EXCHANGES) {
            assertThat(registry.get(SenderExchangeWatcher.MISSING_GAUGE).tag("exchange", exchange).gauge().value())
                    .as("gauge for " + exchange + " exists and a broker that cannot be reached is not reported as 'missing'").isZero();
        }
        // a check against an unreachable broker must not throw
        watcher.check();
    }
}
