package org.kshrd.webblock.project.application.access.model;

import com.fasterxml.jackson.annotation.JsonInclude;

/** The answer's data: {"allowed": true} or {"allowed": false, "reason": ...}. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record AccessDecision(boolean allowed, AccessDenialReason reason) {

    public static final AccessDecision ALLOWED = new AccessDecision(true, null);

    public static AccessDecision denied(AccessDenialReason reason) {
        return new AccessDecision(false, reason);
    }
}
