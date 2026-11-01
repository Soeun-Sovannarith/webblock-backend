package org.kshrd.webblock.project.application.payment.model;

/** How Payment answered a generation admission (contract 2.3), or why it did not. */
public enum GenerationAdmissionOutcome {
    /** Payment admitted the generation. */
    ADMITTED,
    /** Payment refused it; the reason is Payment's own. */
    REFUSED,
    /** Payment did not decide: a timeout, a connection failure or a 5xx. Trying again later may help. */
    UNAVAILABLE,
    /** Any other answer; trying again would not help. */
    ERROR
}
