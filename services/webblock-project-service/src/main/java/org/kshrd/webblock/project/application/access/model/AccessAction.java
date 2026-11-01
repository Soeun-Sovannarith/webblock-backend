package org.kshrd.webblock.project.application.access.model;

import java.util.Arrays;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * Every action the access check accepts, over REST and gRPC CheckAccess alike (FR-32), named as callers send it. A
 * value not listed here is refused as bad input, which Provisioning treats as "access unavailable" (fail closed). The
 * list equals access/actions.v1.json of the pinned contracts, names and write flags (TR-27, AccessActionListTest).
 * The file's admin_allowed flags are for callers only: Project gives an admin nothing more (FR-33).
 */
public enum AccessAction {
    DEPLOY_RELEASE(true),
    PROMOTE_RELEASE(true),
    ROLLBACK_RELEASE(true),
    BUILD_RELEASE(true),
    MANAGE_SITE(true),
    GET_RELEASE(false),
    GET_COMMAND(false),
    GET_DEPLOYMENT(false),
    GET_RELEASE_DIAGNOSTICS(false),
    LIST_RELEASES(false),
    LIST_COMMANDS(false);

    private static final Map<String, AccessAction> BY_WIRE_NAME =
            Arrays.stream(values()).collect(Collectors.toUnmodifiableMap(AccessAction::wireName, Function.identity()));

    private final boolean write;

    AccessAction(boolean write) {
        this.write = write;
    }

    /**
     * The {@code write} flag of access/actions.v1.json: true for an action that changes state, which a deleted or
     * suspended project refuses; false for a get or list action, allowed there (design G21).
     */
    public boolean isWrite() {
        return write;
    }

    /**
     * The name callers send: the constant in lower case. The one place the wire name is stated: REST and gRPC both
     * read it through {@link #fromWireName}.
     */
    public String wireName() {
        return name().toLowerCase(Locale.ROOT);
    }

    /** The action with exactly this name; any other text, a different case included, is none. */
    public static Optional<AccessAction> fromWireName(String name) {
        return name == null ? Optional.empty() : Optional.ofNullable(BY_WIRE_NAME.get(name));
    }
}
