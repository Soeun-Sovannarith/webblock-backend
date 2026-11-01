package org.kshrd.webblock.project.application.project.model;

/**
 * Whether a sub-domain can be used for a new website (FR-13): the FR-13 rule refuses it (INVALID, reserved names
 * included), any project holds it, a deleted one included (TAKEN), or neither (AVAILABLE).
 */
public enum SubdomainAvailability {
    AVAILABLE,
    TAKEN,
    INVALID
}
