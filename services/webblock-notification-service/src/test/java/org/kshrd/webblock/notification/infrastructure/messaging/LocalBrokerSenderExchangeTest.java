package org.kshrd.webblock.notification.infrastructure.messaging;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.kshrd.webblock.notification.NotificationServiceApplication;
import org.springframework.amqp.core.QueueInformation;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitAdmin;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;

import java.time.Duration;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

/**
 * Who owns the sender exchanges, checked on a real broker (LOCAL Docker RabbitMQ only, throw-away vhost per scenario).
 * Opt-in: NOTIFICATION_BROKER_IT=local. See LocalBrokerSupport for the safety rules.
 * The service is started like the real application, with command-line arguments (they win over any environment variable or
 * application.yml value), so these tests can never reach another broker.
 */
@EnabledIfEnvironmentVariable(named = "NOTIFICATION_BROKER_IT", matches = "local")
class LocalBrokerSenderExchangeTest {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final List<String> MAIN_QUEUES = List.of(RabbitMQConfig.SOURCE_PUBLISHED_QUEUE, RabbitMQConfig.PUBLISH_COMPLETED_QUEUE,
            RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE, RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE, RabbitMQConfig.PAYMENT_DUE_QUEUE);
    private static final List<String> SENDER_EXCHANGES = List.of(
            RabbitMQConfig.PROJECT_EXCHANGE, RabbitMQConfig.PROVISIONING_EXCHANGE, RabbitMQConfig.PAYMENT_EXCHANGE);

    private static ConfigurableApplicationContext start(String vhost, boolean declareSenderExchanges) {
        return new SpringApplicationBuilder(NotificationServiceApplication.class).run(
                "--server.port=0",
                "--spring.main.banner-mode=off",
                "--eureka.client.enabled=false",
                "--spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
                "--knock.api-key=dummy-key-not-a-real-credential",
                "--spring.rabbitmq.host=" + LocalBrokerSupport.HOST,
                "--spring.rabbitmq.port=" + LocalBrokerSupport.AMQP_PORT,
                "--spring.rabbitmq.username=" + LocalBrokerSupport.USER,
                "--spring.rabbitmq.password=" + LocalBrokerSupport.PASSWORD,
                "--spring.rabbitmq.virtual-host=" + vhost,
                "--webblock.notification.rabbitmq.declare-sender-exchanges=" + declareSenderExchanges,
                "--webblock.notification.rabbitmq.sender-exchange-check-interval=2s");
    }

    /** An independent connection to the vhost, to look at the broker without going through the service. */
    private static CachingConnectionFactory observerConnection(String vhost) {
        CachingConnectionFactory factory = new CachingConnectionFactory(LocalBrokerSupport.HOST, LocalBrokerSupport.AMQP_PORT);
        factory.setUsername(LocalBrokerSupport.USER);
        factory.setPassword(LocalBrokerSupport.PASSWORD);
        factory.setVirtualHost(vhost);
        return factory;
    }

    private static int consumers(RabbitAdmin admin, String queue) {
        QueueInformation info = admin.getQueueInfo(queue);
        return info == null ? -1 : info.getConsumerCount();
    }

    /** Bindings from the sender exchanges to our (non-dead-letter) queues. */
    private static int bindingsToOurQueues(String vhost) throws Exception {
        int bindings = 0;
        for (String exchange : SENDER_EXCHANGES) {
            JsonNode found;
            try {
                found = JSON.readTree(LocalBrokerSupport.management("GET", "/api/exchanges/" + vhost + "/" + exchange + "/bindings/source", null));
            } catch (IllegalStateException exchangeMissing) {
                continue;
            }
            for (JsonNode binding : found) {
                String destination = binding.path("destination").asText();
                if (destination.startsWith("notification.") && !destination.endsWith(".dlq")) {
                    bindings++;
                }
            }
        }
        return bindings;
    }

    private static double missingGauge(ConfigurableApplicationContext context, String exchange) {
        return context.getBean(MeterRegistry.class).get(SenderExchangeWatcher.MISSING_GAUGE).tag("exchange", exchange).gauge().value();
    }

    private static void log(String line) {
        System.out.println("[exchange-it] " + line);
    }

    @Test
    @DisplayName("bindsOnItsOwnWhenTheSendersCreateTheirExchangesAfterTheServiceStarted")
    void healsWhenTheSenderExchangesAppearLater() throws Exception {
        String vhost = LocalBrokerSupport.newVhostName();
        LocalBrokerSupport.createVhost(vhost);
        CachingConnectionFactory observer = observerConnection(vhost);
        ConfigurableApplicationContext context = null;
        try {
            RabbitAdmin admin = new RabbitAdmin(observer);
            context = start(vhost, false);
            ConfigurableApplicationContext running = context;

            // 1. no sender exchange exists: the service says so, and is bound to nothing
            await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> {
                for (String exchange : SENDER_EXCHANGES) {
                    assertThat(missingGauge(running, exchange)).as("gauge for " + exchange).isEqualTo(1);
                }
            });
            assertThat(bindingsToOurQueues(vhost)).isZero();
            assertThat(SENDER_EXCHANGES).allSatisfy(e -> assertThat(exchangeExists(vhost, e)).as(e + " must not be created by the service").isFalse());
            log("A. exchanges missing: gauge=1 for all three, 0 bindings, and the service created none of the exchanges");

            // 2. the senders start and create their exchanges: no restart, the service binds by itself
            LocalBrokerSupport.createSenderExchanges(vhost);
            await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(bindingsToOurQueues(vhost)).isEqualTo(5));
            for (String exchange : SENDER_EXCHANGES) {
                assertThat(missingGauge(running, exchange)).isZero();
            }
            MAIN_QUEUES.forEach(q -> assertThat(consumers(admin, q)).as("consumers on " + q).isEqualTo(1));
            log("A. senders created their exchanges: 5 bindings appeared without a restart, gauge back to 0, 1 consumer per queue");
        } finally {
            if (context != null) {
                context.close();
            }
            observer.destroy();
            LocalBrokerSupport.deleteVhost(vhost);
        }
    }

    @Test
    @DisplayName("showsWhyTheServiceMustNotRedeclareAnExchangeTheSenderDeclaredDifferently")
    void redeclaringWithOtherSettingsBreaksStartup() throws Exception {
        String vhost = LocalBrokerSupport.newVhostName();
        LocalBrokerSupport.createVhost(vhost);
        // a sender that declared its exchange NOT durable; the old behaviour declared it durable
        LocalBrokerSupport.createExchange(vhost, RabbitMQConfig.PROJECT_EXCHANGE, false, "{}");
        LocalBrokerSupport.createExchange(vhost, RabbitMQConfig.PROVISIONING_EXCHANGE, true, "{}");
        LocalBrokerSupport.createExchange(vhost, RabbitMQConfig.PAYMENT_EXCHANGE, true, "{}");
        CachingConnectionFactory observer = observerConnection(vhost);
        try {
            RabbitAdmin admin = new RabbitAdmin(observer);

            assertThatThrownBy(() -> start(vhost, true).close()).isInstanceOf(Exception.class);

            MAIN_QUEUES.forEach(q -> assertThat(consumers(admin, q)).as("queue " + q + " (absent = -1)").isEqualTo(-1));
            log("B. declare-sender-exchanges=true against a differently declared exchange: startup FAILED and no queue was declared");
        } finally {
            observer.destroy();
            LocalBrokerSupport.deleteVhost(vhost);
        }
    }

    @Test
    @DisplayName("bindsToTheSendersExchangesWithoutChangingThemWhateverSettingsTheySelected")
    void leavesTheSendersExchangesAlone() throws Exception {
        String vhost = LocalBrokerSupport.newVhostName();
        LocalBrokerSupport.createVhost(vhost);
        LocalBrokerSupport.createExchange(vhost, RabbitMQConfig.PROJECT_EXCHANGE, false, "{}");   // odd settings, on purpose
        LocalBrokerSupport.createExchange(vhost, RabbitMQConfig.PROVISIONING_EXCHANGE, true, "{}");
        LocalBrokerSupport.createExchange(vhost, RabbitMQConfig.PAYMENT_EXCHANGE, true, "{}");
        CachingConnectionFactory observer = observerConnection(vhost);
        ConfigurableApplicationContext context = null;
        try {
            RabbitAdmin admin = new RabbitAdmin(observer);
            context = start(vhost, false);

            await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(bindingsToOurQueues(vhost)).isEqualTo(5));
            MAIN_QUEUES.forEach(q -> assertThat(consumers(admin, q)).as("consumers on " + q).isEqualTo(1));
            JsonNode projectEvents = JSON.readTree(LocalBrokerSupport.management("GET", "/api/exchanges/" + vhost + "/" + RabbitMQConfig.PROJECT_EXCHANGE, null));
            assertThat(projectEvents.path("durable").asBoolean()).as("the sender's exchange is exactly as the sender declared it").isFalse();
            log("C. exchanges declared by the senders with their own settings: 5 bindings, 1 consumer per queue, exchange left untouched");
        } finally {
            if (context != null) {
                context.close();
            }
            observer.destroy();
            LocalBrokerSupport.deleteVhost(vhost);
        }
    }

    private static boolean exchangeExists(String vhost, String exchange) {
        try {
            LocalBrokerSupport.management("GET", "/api/exchanges/" + vhost + "/" + exchange, null);
            return true;
        } catch (IllegalStateException notFound) {
            return false;
        }
    }
}
