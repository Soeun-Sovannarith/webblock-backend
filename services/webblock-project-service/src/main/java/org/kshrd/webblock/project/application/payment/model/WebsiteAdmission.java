package org.kshrd.webblock.project.application.payment.model;

/**
 * Payment contract 2.1, the part this service acts on: reason is set exactly when the decision is REFUSED. The plan
 * and usage fields of the answer are not read; nothing here uses them.
 */
public record WebsiteAdmission(AdmissionDecision decision, AdmissionRefusal reason) {}
