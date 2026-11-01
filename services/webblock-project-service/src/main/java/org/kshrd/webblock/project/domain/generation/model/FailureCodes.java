package org.kshrd.webblock.project.domain.generation.model;

import java.util.Locale;
import java.util.regex.Pattern;

/** The failure codes stored on a failed generation request (spec 5.3.4, J2-17). */
public final class FailureCodes {

    public static final String PROJECT_UNAVAILABLE = "project_unavailable";
    public static final String ADMISSION_UNAVAILABLE = "admission_unavailable";
    public static final String ADMISSION_ERROR = "admission_error";

    private static final Pattern STORABLE = Pattern.compile("[a-z_]{1,64}");

    private FailureCodes() {}

    /**
     * The code for a refusal reason from Payment: the reason lower-cased, or {@link #ADMISSION_ERROR} when it is
     * null or its lower case is not 1 to 64 characters of a-z and underscore.
     */
    public static String forRefusal(String reason) {
        if (reason == null) {
            return ADMISSION_ERROR;
        }
        String code = reason.toLowerCase(Locale.ROOT);
        return STORABLE.matcher(code).matches() ? code : ADMISSION_ERROR;
    }
}
