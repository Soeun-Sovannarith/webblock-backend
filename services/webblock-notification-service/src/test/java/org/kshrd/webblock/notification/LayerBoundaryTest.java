package org.kshrd.webblock.notification;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * java-layout.md: outside systems are reached through infrastructure/client. The application and common layers
 * must not import the Knock SDK, and the application layer must not import the RabbitMQ client either.
 */
class LayerBoundaryTest {

    private static final Path MAIN = Path.of("src/main/java/org/kshrd/webblock/notification");

    private static List<String> importsIn(String layer, String forbiddenPrefix) throws IOException {
        Path root = MAIN.resolve(layer);
        if (!Files.exists(root)) {
            return List.of();
        }
        try (Stream<Path> files = Files.walk(root)) {
            return files.filter(p -> p.toString().endsWith(".java"))
                    .flatMap(p -> {
                        try {
                            return Files.readAllLines(p).stream()
                                    .filter(line -> line.startsWith("import " + forbiddenPrefix))
                                    .map(line -> root.relativize(p) + ": " + line);
                        } catch (IOException e) {
                            throw new IllegalStateException(e);
                        }
                    })
                    .toList();
        }
    }

    @Test
    @DisplayName("keepsTheKnockSdkOutOfTheApplicationAndCommonLayers")
    void knockSdkOnlyInInfrastructure() throws IOException {
        assertThat(importsIn("application", "app.knock.")).isEmpty();
        assertThat(importsIn("common", "app.knock.")).isEmpty();
        assertThat(importsIn("presentation", "app.knock.")).isEmpty();
    }

    @Test
    @DisplayName("keepsTheRabbitClientOutOfTheApplicationLayer")
    void rabbitClientNotInApplication() throws IOException {
        assertThat(importsIn("application", "org.springframework.amqp.")).isEmpty();
    }
}
