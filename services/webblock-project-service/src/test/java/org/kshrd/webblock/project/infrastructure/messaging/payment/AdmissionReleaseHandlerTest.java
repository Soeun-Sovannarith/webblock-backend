package org.kshrd.webblock.project.infrastructure.messaging.payment;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.payment.job.AdmissionReleaseJob;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;

class AdmissionReleaseHandlerTest {

    private final PaymentAdmissions payment = mock(PaymentAdmissions.class);
    private final AdmissionReleaseHandler handler = new AdmissionReleaseHandler(payment);

    @Test
    void everyContractAnswerCompletesTheJob() {
        UUID project = UUID.randomUUID();
        for (AdmissionReleaseOutcome answer : AdmissionReleaseOutcome.values()) {
            when(payment.releaseWebsiteAdmission(project)).thenReturn(answer);
            handler.handle(new AdmissionReleaseJob(project), null);
        }
    }

    @Test
    void anUnavailablePaymentIsRetriedWithinABoundedBudget() {
        UUID project = UUID.randomUUID();
        when(payment.releaseWebsiteAdmission(project)).thenThrow(new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE));
        assertThatThrownBy(() -> handler.handle(new AdmissionReleaseJob(project), null))
                .isInstanceOf(WebBlockException.class);
        assertThat(handler.getRetryPolicy().maxRetries()).isEqualTo(AdmissionReleaseHandler.MAX_RETRIES);
    }
}
