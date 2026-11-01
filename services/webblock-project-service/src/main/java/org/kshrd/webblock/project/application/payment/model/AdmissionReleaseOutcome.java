package org.kshrd.webblock.project.application.payment.model;

/** Payment contract 2.2: 204, 404 NOT_ADMITTED, 409 PROJECT_IN_USE. */
public enum AdmissionReleaseOutcome {
    RELEASED,
    NOT_ADMITTED,
    PROJECT_IN_USE
}
