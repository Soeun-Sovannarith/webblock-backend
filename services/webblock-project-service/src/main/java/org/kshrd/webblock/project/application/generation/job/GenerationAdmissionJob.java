package org.kshrd.webblock.project.application.generation.job;

import java.util.UUID;

/** The outbox payload of a request's Payment admission job: the request ID only. */
public record GenerationAdmissionJob(UUID generationRequestId) {

    /** Key generation:<id>: a request's job never waits behind its project's events. */
    public String outboxKey() {
        return "generation:" + generationRequestId;
    }
}
