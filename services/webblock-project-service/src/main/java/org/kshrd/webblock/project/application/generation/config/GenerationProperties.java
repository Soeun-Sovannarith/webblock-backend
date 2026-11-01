package org.kshrd.webblock.project.application.generation.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * webblock.generation. {@code apiContractVersion} is the site-side CRUD and auth API version the generated code
 * targets (not the business type); it is stored in a varchar(100) column. {@code admissionWindow} is how long after
 * capture Payment admission retries run. {@code stuckCaptureMargin} is the grace after that window before the sweeper
 * fails a request still {@code captured} (default 5 minutes); {@code stuckSweepInterval} is the pause between two
 * sweeps (default 1 minute).
 */
@ConfigurationProperties("webblock.generation")
public record GenerationProperties(
        String apiContractVersion, Duration admissionWindow, Duration stuckCaptureMargin, Duration stuckSweepInterval) {

    private static final int MAX_API_CONTRACT_VERSION_LENGTH = 100;
    private static final Duration DEFAULT_STUCK_CAPTURE_MARGIN = Duration.ofMinutes(5);
    private static final Duration DEFAULT_STUCK_SWEEP_INTERVAL = Duration.ofMinutes(1);

    public GenerationProperties {
        if (apiContractVersion == null || apiContractVersion.isBlank()) {
            throw new IllegalArgumentException("webblock.generation.api-contract-version must not be blank");
        }
        if (apiContractVersion.length() > MAX_API_CONTRACT_VERSION_LENGTH) {
            throw new IllegalArgumentException(
                    "webblock.generation.api-contract-version must be at most 100 characters");
        }
        if (admissionWindow == null || admissionWindow.isZero() || admissionWindow.isNegative()) {
            throw new IllegalArgumentException("webblock.generation.admission-window must be positive");
        }
        stuckCaptureMargin = stuckCaptureMargin == null ? DEFAULT_STUCK_CAPTURE_MARGIN : stuckCaptureMargin;
        stuckSweepInterval = stuckSweepInterval == null ? DEFAULT_STUCK_SWEEP_INTERVAL : stuckSweepInterval;
        if (stuckCaptureMargin.isNegative()) {
            throw new IllegalArgumentException("webblock.generation.stuck-capture-margin must not be negative");
        }
        if (stuckSweepInterval.isZero() || stuckSweepInterval.isNegative()) {
            throw new IllegalArgumentException("webblock.generation.stuck-sweep-interval must be positive");
        }
    }
}
