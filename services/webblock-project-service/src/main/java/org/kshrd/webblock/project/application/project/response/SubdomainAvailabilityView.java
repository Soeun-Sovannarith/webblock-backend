package org.kshrd.webblock.project.application.project.response;

import org.kshrd.webblock.project.application.project.model.SubdomainAvailability;

/**
 * The answer to a sub-domain check (FR-13): the label as given, whether it can be used, and else why: {@code TAKEN} or
 * {@code INVALID}. It never says anything about the project that holds it.
 */
public record SubdomainAvailabilityView(String subdomain, boolean available, String reason) {

    public static SubdomainAvailabilityView of(String subdomain, SubdomainAvailability availability) {
        return availability == SubdomainAvailability.AVAILABLE
                ? new SubdomainAvailabilityView(subdomain, true, null)
                : new SubdomainAvailabilityView(subdomain, false, availability.name());
    }
}
