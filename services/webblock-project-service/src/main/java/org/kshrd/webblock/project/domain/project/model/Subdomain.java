package org.kshrd.webblock.project.domain.project.model;

import java.util.Collection;
import java.util.regex.Pattern;

/**
 * The one sub-domain rule (FR-13, decision 0017): one DNS label of 3 to 55 lowercase letters, digits and dashes, not
 * starting or ending with a dash, not ending with {@code -preview} (the preview host is the label plus that suffix),
 * not starting with {@code xn--} (a punycode label), and not a reserved name. The column projects.subdomain stays
 * VARCHAR(63) (Notion "2 · Project"); {@link #MAX_LENGTH} is that column, {@link #MAX_LABEL_LENGTH} is the rule.
 */
public final class Subdomain {

    /** The column width and the longest DNS label; the site domain limit is derived from it (253 = 63 + 1 + 189). */
    public static final int MAX_LENGTH = 63;

    /** The longest label a client may choose: 55 plus {@code -preview} (8) is 63. */
    public static final int MAX_LABEL_LENGTH = 55;

    private static final String PREVIEW_SUFFIX = "-preview";
    private static final String PUNYCODE_PREFIX = "xn--";

    private static final Pattern LABEL = Pattern.compile("^[a-z0-9][a-z0-9-]{1,53}[a-z0-9]$");

    private Subdomain() {}

    /** One DNS label as the rule allows it; the length is checked first, so a long value costs nothing. */
    public static boolean isLabel(String value) {
        return value != null
                && value.length() >= 3
                && value.length() <= MAX_LABEL_LENGTH
                && LABEL.matcher(value).matches()
                && !value.endsWith(PREVIEW_SUFFIX)
                && !value.startsWith(PUNYCODE_PREFIX);
    }

    /** A label that is not one of the reserved names. */
    public static boolean isAllowed(String value, Collection<String> reserved) {
        return isLabel(value) && !reserved.contains(value);
    }
}
