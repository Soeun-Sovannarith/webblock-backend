package org.kshrd.webblock.project.application.generation.job;

import java.time.Clock;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.kshrd.webblock.project.application.generation.config.GenerationProperties;
import org.kshrd.webblock.project.application.generation.handler.GenerationTransitions;
import org.kshrd.webblock.project.domain.generation.model.FailureCodes;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationRequestRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Limit;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * FR-28: fails a request still {@code captured} longer than the admission window plus a margin with
 * {@code admission_unavailable}, so a lost admission job never holds the one-in-flight slot of its website. Each
 * request moves in its own transaction through the conditional update, so concurrent sweeps never both win.
 */
@Component
public class StuckCaptureSweeper {

    static final int BATCH = 100;

    private static final Logger LOG = LoggerFactory.getLogger(StuckCaptureSweeper.class);

    private final GenerationRequestRepository requests;
    private final GenerationTransitions transitions;
    private final TransactionTemplate tx;
    private final GenerationProperties properties;
    private final Clock clock;

    public StuckCaptureSweeper(
            GenerationRequestRepository requests,
            GenerationTransitions transitions,
            TransactionTemplate tx,
            GenerationProperties properties,
            Clock clock) {
        this.requests = requests;
        this.transitions = transitions;
        this.tx = tx;
        this.properties = properties;
        this.clock = clock;
    }

    /** Runs every {@code webblock.generation.stuck-sweep-interval}; the wait before the first run is the interval. */
    @Scheduled(
            fixedDelayString = "${webblock.generation.stuck-sweep-interval:1m}",
            initialDelayString = "${webblock.generation.stuck-sweep-interval:1m}")
    void scheduledSweep() {
        try {
            sweep();
        } catch (RuntimeException e) {
            LOG.error("The stuck-capture sweep failed; it runs again at the next interval", e);
        }
    }

    /** Fails one batch of stuck requests; returns how many this call moved. */
    public int sweep() {
        Instant cutoff = clock.instant().minus(properties.admissionWindow()).minus(properties.stuckCaptureMargin());
        List<UUID> stuck = requests.findStuckCaptured(GenerationRequestStatus.captured, cutoff, Limit.of(BATCH));
        int moved = 0;
        for (UUID id : stuck) {
            try {
                if (Boolean.TRUE.equals(tx.execute(status -> transitions.move(
                        id,
                        GenerationRequestStatus.captured,
                        GenerationRequestStatus.failed,
                        FailureCodes.ADMISSION_UNAVAILABLE)))) {
                    moved++;
                    LOG.info("Failed generation request {}: captured past the admission window", id);
                }
            } catch (RuntimeException e) {
                LOG.warn("Could not fail stuck generation request {}; the next sweep retries it", id, e);
            }
        }
        return moved;
    }
}
