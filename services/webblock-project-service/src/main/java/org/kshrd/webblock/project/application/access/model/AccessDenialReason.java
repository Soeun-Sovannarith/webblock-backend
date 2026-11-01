package org.kshrd.webblock.project.application.access.model;

/** Why the access check said no (communication note section 3, plus NOT_FOUND for an unknown project). */
public enum AccessDenialReason {
    NOT_FOUND,
    NOT_MANAGER,
    PROJECT_DELETED,
    PROJECT_SUSPENDED
}
