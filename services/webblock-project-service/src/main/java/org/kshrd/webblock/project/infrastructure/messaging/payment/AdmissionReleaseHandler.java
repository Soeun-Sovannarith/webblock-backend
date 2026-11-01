package org.kshrd.webblock.project.infrastructure.messaging.payment;

import io.namastack.outbox.handler.OutboxRecordMetadata;
import io.namastack.outbox.handler.OutboxTypedHandler;
import io.namastack.outbox.retry.OutboxRetryAware;
import io.namastack.outbox.retry.OutboxRetryPolicy;
import java.time.Duration;
import org.kshrd.webblock.project.application.payment.job.AdmissionReleaseJob;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/** Releases an unused website admission; every contract answer ends the job, an unavailable Payment retries. */
@Component
class AdmissionReleaseHandler implements OutboxTypedHandler<AdmissionReleaseJob>, OutboxRetryAware {

    static final int MAX_RETRIES = 12;

    private static final Logger log = LoggerFactory.getLogger(AdmissionReleaseHandler.class);

    private final PaymentAdmissions payment;

    AdmissionReleaseHandler(PaymentAdmissions payment) {
        this.payment = payment;
    }

    @Override
    public void handle(AdmissionReleaseJob job, OutboxRecordMetadata metadata) {
        AdmissionReleaseOutcome outcome = payment.releaseWebsiteAdmission(job.projectId());
        log.info("Website admission release for project {}: {}", job.projectId(), outcome);
    }

    @Override
    public OutboxRetryPolicy getRetryPolicy() {
        return OutboxRetryPolicy.builder()
                .maxRetries(MAX_RETRIES)
                .exponentialBackoff(Duration.ofSeconds(2), 2.0, Duration.ofMinutes(10))
                .build();
    }
}
