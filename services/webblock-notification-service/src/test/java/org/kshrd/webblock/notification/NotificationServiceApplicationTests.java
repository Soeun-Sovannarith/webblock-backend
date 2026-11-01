package org.kshrd.webblock.notification;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.infrastructure.messaging.SenderExchangeWatcher;
import org.springframework.amqp.core.MessageListenerContainer;
import org.springframework.amqp.rabbit.listener.RabbitListenerEndpointRegistry;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.assertj.core.api.Assertions.assertThat;

// Tests must not need a running discovery-service, and must never attach a consumer to a broker that happens to run
// on localhost: the listeners are not started here (the opt-in LocalBroker* tests use their own throw-away vhost).
@SpringBootTest(properties = {"eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "spring.rabbitmq.listener.simple.auto-startup=false"})
class NotificationServiceApplicationTests {

    @Autowired
    RabbitListenerEndpointRegistry listenerRegistry;

    @Autowired
    ObjectProvider<SenderExchangeWatcher> senderExchangeWatcher;

    @Test
    void contextLoads() {
    }

    @Test
    @DisplayName("hasFiveListenersAndNoneIsRunningSoNoConsumerIsAttachedToAnyBroker")
    void listenersAreDeclaredButNotStarted() {
        assertThat(listenerRegistry.getListenerContainers()).hasSize(5);
        for (MessageListenerContainer container : listenerRegistry.getListenerContainers()) {
            assertThat(container.isRunning()).as("container " + container).isFalse();
        }
    }

    @Test
    @DisplayName("doesNotWatchTheSenderExchangesWhenItDoesNotConsume")
    void noWatcherWithoutListeners() {
        assertThat(senderExchangeWatcher.getIfAvailable()).isNull();
    }
}
