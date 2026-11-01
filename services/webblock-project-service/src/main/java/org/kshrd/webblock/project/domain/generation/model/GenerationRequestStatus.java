package org.kshrd.webblock.project.domain.generation.model;

import java.util.EnumSet;
import java.util.Set;

/** The status of a generation request and the moves between statuses (spec 5.3.5). */
public enum GenerationRequestStatus {
    captured,
    generating,
    publishing,
    published,
    failed;

    /** Whether a request in this status may move to {@code next}. */
    public boolean canMoveTo(GenerationRequestStatus next) {
        return successors().contains(next);
    }

    /** published and failed accept no further move. */
    public boolean isFinal() {
        return successors().isEmpty();
    }

    private Set<GenerationRequestStatus> successors() {
        return switch (this) {
            case captured -> EnumSet.of(generating, failed);
            case generating -> EnumSet.of(publishing, failed);
            case publishing -> EnumSet.of(published, failed);
            case published, failed -> EnumSet.noneOf(GenerationRequestStatus.class);
        };
    }
}
