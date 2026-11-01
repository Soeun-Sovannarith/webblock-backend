package org.kshrd.webblock.project.infrastructure.config.payment;

import java.net.URI;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * webblock.payment. mode: http (default) or stand-in (prototype profile only). The timeout applies per attempt: with
 * the single idempotent retry the worst case is about 2 x 2 s. No credential: the calls carry no token, and Payment's
 * sidecar admits this service by its mesh identity (decision 0002).
 */
@ConfigurationProperties("webblock.payment")
public record PaymentProperties(String mode, URI baseUrl, Duration timeout, String standInFile) {

    public PaymentProperties {
        mode = mode == null ? "http" : mode;
        timeout = timeout == null ? Duration.ofSeconds(2) : timeout;
        standInFile = standInFile == null ? "" : standInFile;
    }
}
