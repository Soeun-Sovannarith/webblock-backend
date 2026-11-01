package org.kshrd.webblock.project.common.domain.support;

import java.util.regex.Pattern;

/** A public page path: "/" or "/" followed by lowercase segments of letters, digits and hyphens. */
public final class PagePaths {

    /** The length of the column {@code project.pages.path}. */
    public static final int MAX_LENGTH = 255;

    /**
     * The same rule as the database constraint {@code pages_path_check}. Private: the match recurses once per segment
     * and overflows the stack at about 20 KB, so it runs only behind the length check of {@link #isValid}.
     */
    private static final Pattern PATTERN = Pattern.compile("^/([a-z0-9-]+(/[a-z0-9-]+)*)?$");

    private PagePaths() {}

    public static boolean isValid(String path) {
        return path != null
                && path.length() <= MAX_LENGTH
                && PATTERN.matcher(path).matches();
    }
}
