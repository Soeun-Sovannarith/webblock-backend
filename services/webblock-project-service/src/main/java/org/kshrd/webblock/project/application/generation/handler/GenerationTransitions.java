package org.kshrd.webblock.project.application.generation.handler;

import java.time.Clock;
import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationRequestRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/** The only way a generation request changes status (spec 5.3.5). */
@Service
public class GenerationTransitions {

    private final GenerationRequestRepository requests;
    private final Clock clock;

    public GenerationTransitions(GenerationRequestRepository requests, Clock clock) {
        this.requests = requests;
        this.clock = clock;
    }

    /**
     * Moves the request from {@code from} to {@code to} if it is still in {@code from}, storing {@code failureCode}
     * (null unless {@code to} is failed). Returns whether the row changed; false means another move came first.
     *
     * @throws IllegalStateException if the state machine has no such move
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public boolean move(
            UUID generationRequestId, GenerationRequestStatus from, GenerationRequestStatus to, String failureCode) {
        if (!from.canMoveTo(to)) {
            throw new IllegalStateException("A generation request cannot move from " + from + " to " + to);
        }
        return requests.transition(generationRequestId, from, to, failureCode, clock.instant()) == 1;
    }
}
