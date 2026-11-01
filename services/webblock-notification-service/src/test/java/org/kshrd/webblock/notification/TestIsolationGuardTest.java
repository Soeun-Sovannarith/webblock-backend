package org.kshrd.webblock.notification;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.SpringProperties;
import org.springframework.core.env.Environment;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Keeps the two protections that stop a test from attaching a consumer to the developer's real RabbitMQ (localhost:5672,
 * vhost "/") from being removed by accident. Why both exist:
 * - spring.properties: Spring's test cache pauses idle contexts and START()s them again on reuse, which starts listener
 *   containers even when auto-startup=false (one real connection per full run, until this was set);
 * - config/application.yml: a context that still tries to reach RabbitMQ goes to a port nobody listens on.
 */
@SpringBootTest(properties = {"eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "spring.rabbitmq.listener.simple.auto-startup=false"})
class TestIsolationGuardTest {

    @Autowired
    Environment environment;

    @Test
    @DisplayName("neverPausesAndResumesCachedContextsBecauseResumingStartsTheListenerContainers")
    void contextPausingIsOff() {
        assertThat(SpringProperties.getProperty("spring.test.context.cache.pause")).isEqualTo("never");
    }

    @Test
    @DisplayName("pointsEveryTestContextAtADeadRabbitMqPortUnlessATestChoosesItsOwn")
    void defaultBrokerPortIsNotTheRealOne() {
        assertThat(environment.getProperty("spring.rabbitmq.port", Integer.class)).isEqualTo(1);
    }
}
