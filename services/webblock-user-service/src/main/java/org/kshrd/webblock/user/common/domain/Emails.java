package org.kshrd.webblock.user.common.domain;

import java.util.Locale;

public final class Emails {

    private Emails() {
    }

    // One email = one account: "Sara@Gmail.com " and "sara@gmail.com" are the same
    public static String normalize(String email) {
        return email == null ? null : email.trim().toLowerCase(Locale.ROOT);
    }
}
