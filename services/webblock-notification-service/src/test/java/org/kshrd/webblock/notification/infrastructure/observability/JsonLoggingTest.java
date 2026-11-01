package org.kshrd.webblock.notification.infrastructure.observability;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.common.presentation.TraceContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.logging.LoggingInitializationContext;
import org.springframework.boot.logging.LoggingSystem;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;

import org.springframework.core.env.StandardEnvironment;

import static org.assertj.core.api.Assertions.assertThat;

/** The "json-logs" profile: one JSON object per line on stdout, carrying the trace fields. */
@SpringBootTest(properties = {"spring.profiles.active=json-logs",
        "eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test",
        "knock.api-key=dummy-key-not-a-real-credential",
        "spring.rabbitmq.listener.simple.auto-startup=false"})
@ExtendWith(OutputCaptureExtension.class)
class JsonLoggingTest {

    private static final Logger LOG = LoggerFactory.getLogger("json.logging.check");

    /** The logging system is shared by the whole test JVM: put the default console format back for the classes that run next. */
    @AfterAll
    static void restoreTheDefaultLogFormat() {
        // Boot keeps the chosen structured format in JVM system properties; without clearing them the re-initialised logging stays JSON
        System.clearProperty("CONSOLE_LOG_STRUCTURED_FORMAT");
        System.clearProperty("FILE_LOG_STRUCTURED_FORMAT");
        LoggingSystem logging = LoggingSystem.get(JsonLoggingTest.class.getClassLoader());
        logging.cleanUp();
        logging.beforeInitialize();
        logging.initialize(new LoggingInitializationContext(new StandardEnvironment()), null, null);
    }

    @Test
    @DisplayName("writesOneJsonObjectPerLineWithTheTraceIdSpanIdAndRequestId")
    void jsonLineCarriesTheTraceFields(CapturedOutput output) throws Exception {
        try (TraceContext.Scope ignored = TraceContext.open("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "req-json-1")) {
            LOG.info("delivery checked");
        }

        String line = output.getOut().lines().filter(l -> l.contains("delivery checked")).findFirst().orElseThrow();
        JsonNode json = new ObjectMapper().readTree(line);

        assertThat(json.path("message").asText()).isEqualTo("delivery checked");
        assertThat(json.path("trace_id").asText()).isEqualTo("4bf92f3577b34da6a3ce929d0e0e4736");
        assertThat(json.path("span_id").asText()).isEqualTo("00f067aa0ba902b7");
        assertThat(json.path("request_id").asText()).isEqualTo("req-json-1");
        assertThat(json.has("@timestamp")).isTrue();
    }

    @Test
    @DisplayName("keepsALogInjectionAttemptInsideOneJsonString")
    void injectedLineBreakStaysInsideTheMessage(CapturedOutput output) throws Exception {
        LOG.info("first line\nforged second line");

        long lines = output.getOut().lines().filter(l -> l.contains("forged second line")).count();
        String line = output.getOut().lines().filter(l -> l.contains("forged second line")).findFirst().orElseThrow();

        assertThat(lines).as("still one physical log line").isEqualTo(1);
        assertThat(new ObjectMapper().readTree(line).path("message").asText()).contains("forged second line");
    }
}
