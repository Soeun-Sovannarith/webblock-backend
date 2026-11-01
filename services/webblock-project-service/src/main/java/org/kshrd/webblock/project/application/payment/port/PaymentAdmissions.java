package org.kshrd.webblock.project.application.payment.port;

import java.util.UUID;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.model.WebsiteAdmission;

/**
 * What the Project service asks Payment (.claude/docs/contracts/payment-contract.md). Never called inside a database
 * transaction. The website methods throw WebBlockException(PAYMENT_UNAVAILABLE) when Payment cannot decide (fail closed).
 */
public interface PaymentAdmissions {

    /** Idempotent on projectId. A repeat with another client answers PROJECT_ID_CONFLICT. */
    WebsiteAdmission admitWebsite(UUID clientUserId, UUID projectId);

    /** Only for an admission whose project will never be created. */
    AdmissionReleaseOutcome releaseWebsiteAdmission(UUID projectId);

    /**
     * Contract 2.3; idempotent on generationRequestId. Never throws for Payment's answers: refusals, timeouts, 5xx and
     * every other answer come back as an outcome.
     */
    GenerationAdmission admitGeneration(UUID clientUserId, UUID projectId, UUID generationRequestId);
}
