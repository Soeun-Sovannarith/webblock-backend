package org.kshrd.webblock.project.common.infrastructure.security;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/** The identity mode, bound strictly: an unknown value fails the start-up instead of selecting some default. */
@ConfigurationProperties("webblock.security")
public record SecurityProperties(@DefaultValue("jwt") IdentityMode identityMode) {

    public enum IdentityMode {
        JWT,
        DEV_TOKENS
    }
}
