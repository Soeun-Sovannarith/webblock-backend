package org.kshrd.webblock.project.application.project.config;

import java.util.List;
import java.util.regex.Pattern;
import org.kshrd.webblock.project.domain.project.model.Subdomain;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * webblock.platform.site-domain: the suffix Provisioning serves sites under; a site is {@code <subdomain>.<siteDomain>}.
 * webblock.platform.reserved-subdomains: names no client may choose; each one is a valid sub-domain label.
 */
@ConfigurationProperties("webblock.platform")
public record PlatformProperties(String siteDomain, List<String> reservedSubdomains) {

    // 253 (the DNS name limit) minus the longest sub-domain and the dot: <subdomain>.<siteDomain> always fits
    static final int MAX_SITE_DOMAIN_LENGTH = 253 - Subdomain.MAX_LENGTH - 1;

    // A lowercase DNS name of two or more labels
    private static final Pattern DOMAIN =
            Pattern.compile("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})$");

    public PlatformProperties {
        if (siteDomain == null
                || siteDomain.length() > MAX_SITE_DOMAIN_LENGTH
                || !DOMAIN.matcher(siteDomain).matches()) {
            throw new IllegalArgumentException(
                    "webblock.platform.site-domain must be a lowercase DNS name of two or more labels and at most "
                            + MAX_SITE_DOMAIN_LENGTH + " characters");
        }
        if (reservedSubdomains == null) {
            reservedSubdomains = List.of();
        }
        for (String name : reservedSubdomains) {
            if (!Subdomain.isLabel(name)) {
                throw new IllegalArgumentException(
                        "webblock.platform.reserved-subdomains must hold sub-domain labels only: 3 to 55 lowercase "
                                + "letters, digits and dashes, not starting or ending with a dash, not ending with "
                                + "-preview, not starting with xn--");
            }
        }
        reservedSubdomains = List.copyOf(reservedSubdomains);
    }
}
