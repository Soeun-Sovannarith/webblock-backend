package org.kshrd.webblock.project.application.payment.model;

/** Payment contract 2.1: why a website is refused. OTHER is any reason Payment adds later. */
public enum AdmissionRefusal {
    WEBSITE_LIMIT_REACHED,
    TRIAL_ENDED,
    NO_ACTIVE_PLAN,
    CLIENT_SUSPENDED,
    OTHER
}
