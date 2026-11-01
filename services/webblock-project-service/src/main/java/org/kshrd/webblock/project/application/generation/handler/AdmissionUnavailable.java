package org.kshrd.webblock.project.application.generation.handler;

import java.util.UUID;

/** Payment did not decide and the admission window is still open: the job is tried again. */
public class AdmissionUnavailable extends RuntimeException {

    public AdmissionUnavailable(UUID generationRequestId) {
        super("Payment admission of generation request " + generationRequestId + " is unavailable; it will be retried");
    }
}
