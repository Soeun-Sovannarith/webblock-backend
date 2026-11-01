package org.kshrd.webblock.project.application.generation.handler;

import java.time.Clock;
import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.application.generation.config.GenerationProperties;
import org.kshrd.webblock.project.application.generation.model.GenerationRequested;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.domain.generation.model.FailureCodes;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationRequestRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The admission job's work (spec 5.3.4): Payment admits a captured request, which then generates, or the request
 * fails with a failure code. Each write is its own short transaction; Payment is called with none open (spec 4.2).
 * Every write moves the request only out of captured, so a replayed or late job never overwrites a later status.
 */
@Service
public class GenerationAdmissionService {

    private static final Logger log = LoggerFactory.getLogger(GenerationAdmissionService.class);

    private final GenerationRequestRepository requests;
    private final GenerationTransitions transitions;
    private final ProjectService projects;
    private final PaymentAdmissions payment;
    private final ApplicationEventPublisher events;
    private final TransactionTemplate transaction;
    private final GenerationProperties properties;
    private final Clock clock;

    public GenerationAdmissionService(
            GenerationRequestRepository requests,
            GenerationTransitions transitions,
            ProjectService projects,
            PaymentAdmissions payment,
            ApplicationEventPublisher events,
            TransactionTemplate transaction,
            GenerationProperties properties,
            Clock clock) {
        this.requests = requests;
        this.transitions = transitions;
        this.projects = projects;
        this.payment = payment;
        this.events = events;
        this.transaction = transaction;
        this.properties = properties;
        this.clock = clock;
    }

    /**
     * One attempt. Returns normally when the job is done (the request generates or fails, or it has left captured).
     *
     * @throws AdmissionUnavailable when Payment did not decide and the window (from capture) is still open
     */
    public void admit(UUID generationRequestId) {
        if (TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException("Generation admission calls Payment and must run outside a transaction");
        }
        Optional<GenerationRequestEntity> found = requests.findById(generationRequestId);
        if (found.isEmpty()) {
            log.warn("Generation request {} does not exist; its admission job ends", generationRequestId);
            return;
        }
        GenerationRequestEntity request = found.get();
        if (request.status() != GenerationRequestStatus.captured) {
            log.info("Generation request {} is {}; its admission job ends", generationRequestId, request.status());
            return;
        }
        Optional<ProjectRecord> project = projects.find(request.projectId());
        if (project.isEmpty() || project.get().readOnly()) {
            fail(generationRequestId, FailureCodes.PROJECT_UNAVAILABLE);
            return;
        }
        GenerationAdmission admission =
                payment.admitGeneration(project.get().ownerUserId(), request.projectId(), generationRequestId);
        switch (admission.outcome()) {
            case ADMITTED -> generate(request);
            case REFUSED -> fail(generationRequestId, FailureCodes.forRefusal(admission.reason()));
            case ERROR -> fail(generationRequestId, FailureCodes.ADMISSION_ERROR);
            case UNAVAILABLE -> {
                if (clock.instant().isBefore(request.createdAt().plus(properties.admissionWindow()))) {
                    log.info(
                            "Payment admission of generation request {} is unavailable; retrying", generationRequestId);
                    throw new AdmissionUnavailable(generationRequestId);
                }
                fail(generationRequestId, FailureCodes.ADMISSION_UNAVAILABLE);
            }
        }
    }

    /**
     * The job's fallback once its retries are spent: admission_unavailable if Payment was unavailable last, otherwise
     * admission_error; only while the request is still captured.
     */
    public void giveUp(UUID generationRequestId, boolean unavailable) {
        fail(generationRequestId, unavailable ? FailureCodes.ADMISSION_UNAVAILABLE : FailureCodes.ADMISSION_ERROR);
    }

    private void generate(GenerationRequestEntity request) {
        UUID generationRequestId = request.generationRequestId();
        // The project is locked and read again here: it may have been suspended or deleted since the first look.
        boolean moved = Boolean.TRUE.equals(transaction.execute(status -> {
            Optional<ProjectRecord> live = projects.lockIfLive(request.projectId());
            if (live.isEmpty() || live.get().readOnly()) {
                transitions.move(
                        generationRequestId,
                        GenerationRequestStatus.captured,
                        GenerationRequestStatus.failed,
                        FailureCodes.PROJECT_UNAVAILABLE);
                return false;
            }
            if (!transitions.move(
                    generationRequestId, GenerationRequestStatus.captured, GenerationRequestStatus.generating, null)) {
                return false;
            }
            events.publishEvent(GenerationRequested.of(
                    request.projectId(), generationRequestId, request.capturedInputHash(), clock.instant()));
            return true;
        }));
        log.info(
                "Generation request {} admitted: {}",
                generationRequestId,
                moved ? "generating" : "failed or no longer captured");
    }

    private void fail(UUID generationRequestId, String failureCode) {
        boolean moved = Boolean.TRUE.equals(transaction.execute(status -> transitions.move(
                generationRequestId, GenerationRequestStatus.captured, GenerationRequestStatus.failed, failureCode)));
        log.info(
                "Generation request {} admission ended: {}",
                generationRequestId,
                moved ? "failed " + failureCode : "no longer captured");
    }
}
