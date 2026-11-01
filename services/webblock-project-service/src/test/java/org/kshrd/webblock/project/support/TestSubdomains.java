package org.kshrd.webblock.project.support;

import java.util.UUID;

/** A sub-domain for a test project that no other row holds: "s-" and the project ID without dashes (FR-13). */
public final class TestSubdomains {

    private TestSubdomains() {}

    public static String of(UUID projectId) {
        return "s-" + projectId.toString().replace("-", "");
    }
}
