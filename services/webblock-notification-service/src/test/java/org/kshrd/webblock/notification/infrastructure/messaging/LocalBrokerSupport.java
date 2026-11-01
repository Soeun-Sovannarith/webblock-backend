package org.kshrd.webblock.notification.infrastructure.messaging;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;
import java.util.UUID;

/**
 * Helpers for the opt-in tests against the LOCAL Docker RabbitMQ (localhost:5672, management on localhost:15672).
 * The host is fixed to localhost on purpose: no environment variable can point these tests at the deployed broker.
 * Every test works in its own throw-away vhost ("notification-it-xxxxxxxx"); only such a vhost is ever deleted.
 */
final class LocalBrokerSupport {

    static final String HOST = "localhost";
    static final int AMQP_PORT = 5672;
    static final String MANAGEMENT = "http://localhost:15672";
    static final String USER = "guest";   // RabbitMQ's default development account, the same default as application.yml
    static final String PASSWORD = "guest";
    static final String VHOST_PREFIX = "notification-it-";

    // HTTP/1.1 on purpose: the JDK client's default h2c upgrade attempt on plain HTTP makes the management API drop the connection
    private static final HttpClient HTTP = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();

    private LocalBrokerSupport() {
    }

    static String newVhostName() {
        return VHOST_PREFIX + UUID.randomUUID().toString().substring(0, 8);
    }

    /** Creates the vhost and gives the test account full rights on it. */
    static void createVhost(String vhost) {
        requireTestVhost(vhost);
        management("PUT", "/api/vhosts/" + vhost, "{}");
        management("PUT", "/api/permissions/" + vhost + "/" + USER, "{\"configure\":\".*\",\"write\":\".*\",\"read\":\".*\"}");
        // Whatever happens to the test (a Spring context that fails to load never reaches its @AfterAll), the vhost goes with the JVM
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            try {
                deleteVhost(vhost);
            } catch (RuntimeException alreadyGoneOrBrokerStopped) {
                // nothing left to clean up
            }
        }));
    }

    /** Deletes the vhost: refuses anything that is not a vhost created by these tests. */
    static void deleteVhost(String vhost) {
        requireTestVhost(vhost);
        management("DELETE", "/api/vhosts/" + vhost, null);
    }

    /** Declares an exchange the way a sending service would (topic). {@code argumentsJson} is the exchange arguments object. */
    static void createExchange(String vhost, String name, boolean durable, String argumentsJson) {
        requireTestVhost(vhost);
        management("PUT", "/api/exchanges/" + vhost + "/" + name,
                "{\"type\":\"topic\",\"durable\":" + durable + ",\"auto_delete\":false,\"internal\":false,\"arguments\":" + argumentsJson + "}");
    }

    /** The three exchanges of the sending services, as they declare them. */
    static void createSenderExchanges(String vhost) {
        createExchange(vhost, RabbitMQConfig.PROJECT_EXCHANGE, true, "{}");
        createExchange(vhost, RabbitMQConfig.PROVISIONING_EXCHANGE, true, "{}");
        createExchange(vhost, RabbitMQConfig.PAYMENT_EXCHANGE, true, "{}");
    }

    static String management(String method, String path, String body) {
        try {
            HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(MANAGEMENT + path))
                    .timeout(Duration.ofSeconds(10))
                    .header("Authorization", "Basic " + Base64.getEncoder().encodeToString((USER + ":" + PASSWORD).getBytes(StandardCharsets.UTF_8)))
                    .header("Content-Type", "application/json");
            request.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
            HttpResponse<String> response = HTTP.send(request.build(), HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() >= 300) {
                throw new IllegalStateException(method + " " + path + " answered " + response.statusCode());
            }
            return response.body();
        } catch (IOException e) {
            throw new IllegalStateException("Cannot reach the local RabbitMQ management API at " + MANAGEMENT
                    + " (" + e.getClass().getSimpleName() + ": " + e.getMessage() + ")", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
    }

    private static void requireTestVhost(String vhost) {
        if (vhost == null || !vhost.startsWith(VHOST_PREFIX)) {
            throw new IllegalArgumentException("Refusing to touch vhost '" + vhost + "': only vhosts created by these tests are allowed");
        }
    }
}
