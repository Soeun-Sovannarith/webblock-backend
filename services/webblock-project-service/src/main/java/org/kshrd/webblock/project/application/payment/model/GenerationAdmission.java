package org.kshrd.webblock.project.application.payment.model;

/** Payment contract 2.3, the part this service acts on. reason is Payment's raw reason, set only for REFUSED. */
public record GenerationAdmission(GenerationAdmissionOutcome outcome, String reason) {

    public static final GenerationAdmission ADMITTED =
            new GenerationAdmission(GenerationAdmissionOutcome.ADMITTED, null);
    public static final GenerationAdmission UNAVAILABLE =
            new GenerationAdmission(GenerationAdmissionOutcome.UNAVAILABLE, null);
    public static final GenerationAdmission ERROR = new GenerationAdmission(GenerationAdmissionOutcome.ERROR, null);

    public static GenerationAdmission refused(String reason) {
        return new GenerationAdmission(GenerationAdmissionOutcome.REFUSED, reason);
    }
}
