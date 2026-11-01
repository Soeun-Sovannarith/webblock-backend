package org.kshrd.webblock.project.infrastructure.standin.payment;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.kshrd.webblock.project.application.payment.model.AdmissionDecision;
import org.kshrd.webblock.project.application.payment.model.AdmissionRefusal;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.model.WebsiteAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.infrastructure.config.StandIn;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

/**
 * PROTOTYPE STAND-IN for Payment (spec section 8): implements the contract's website admission exactly (idempotent
 * on projectId, a refusal records nothing, release 2.2), admitting everything unless an optional, reloadable file
 * lists refused clients: {"websiteRefusals":[{"clientUserId":"<uuid>","reason":"<contract reason>"}]}. State is in
 * memory and lost on restart. Generation admission (contract 2.3) is the same: idempotent on generationRequestId (a
 * repeat for the same client and project returns the first ADMITTED; the same id for another client or project is
 * ERROR, as Payment's IDEMPOTENCY_CONFLICT), refusals from the file, which holds both lists:
 * {"websiteRefusals":[{"clientUserId":"<uuid>","reason":"<contract 2.1 reason>"}],
 * "generationRefusals":[{"clientUserId":"<uuid>","reason":"<contract 2.3 reason>"}]}. An unreadable, invalid or oversized (over 64 KB) file fails closed (PAYMENT_UNAVAILABLE; UNAVAILABLE for a generation).
 */
@org.springframework.context.annotation.Profile("prototype")
public final class PaymentStandIn implements PaymentAdmissions, StandIn {

    static final long MAX_FILE_BYTES = 65_536;

    private static final Logger log = LoggerFactory.getLogger(PaymentStandIn.class);

    record Refusal(UUID clientUserId, String reason) {}

    record RefusalFile(List<Refusal> websiteRefusals, List<Refusal> generationRefusals) {}

    private static final Set<String> GENERATION_REASONS = Set.of(
            "TRIAL_GENERATION_LIMIT_REACHED",
            "TRIAL_ENDED",
            "NO_ACTIVE_PLAN",
            "CLIENT_SUSPENDED",
            "PROJECT_NOT_ADMITTED");

    private record GenerationKey(UUID clientUserId, UUID projectId) {}

    private record Admission(UUID clientUserId, WebsiteAdmission decision) {}

    private record Loaded(
            long modified, long size, Map<UUID, AdmissionRefusal> refusals, Map<UUID, String> generationRefusals) {}

    private final Path file;
    private final JsonMapper json;
    private final Map<UUID, Admission> admissions = new ConcurrentHashMap<>();
    private final Map<UUID, GenerationKey> admittedGenerations = new ConcurrentHashMap<>();
    private Loaded loaded;

    /** file: the refusal file, or blank for none. */
    public PaymentStandIn(String file, JsonMapper json) {
        this.file = file == null || file.isBlank() ? null : Path.of(file);
        this.json = json;
    }

    @Override
    public synchronized WebsiteAdmission admitWebsite(UUID clientUserId, UUID projectId) {
        Admission prior = admissions.get(projectId);
        if (prior != null) {
            if (!prior.clientUserId().equals(clientUserId)) {
                throw new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT);
            }
            return prior.decision();
        }
        AdmissionRefusal refusal = load().refusals().get(clientUserId);
        if (refusal != null) {
            log.info("Prototype Payment stand-in refused a website for project {}", projectId);
            return new WebsiteAdmission(AdmissionDecision.REFUSED, refusal);
        }
        WebsiteAdmission admitted = new WebsiteAdmission(AdmissionDecision.ADMITTED, null);
        admissions.put(projectId, new Admission(clientUserId, admitted));
        log.info("Prototype Payment stand-in admitted a website for project {}", projectId);
        return admitted;
    }

    @Override
    public synchronized AdmissionReleaseOutcome releaseWebsiteAdmission(UUID projectId) {
        return admissions.remove(projectId) != null
                ? AdmissionReleaseOutcome.RELEASED
                : AdmissionReleaseOutcome.NOT_ADMITTED;
    }

    @Override
    public synchronized GenerationAdmission admitGeneration(
            UUID clientUserId, UUID projectId, UUID generationRequestId) {
        GenerationKey key = new GenerationKey(clientUserId, projectId);
        GenerationKey prior = admittedGenerations.get(generationRequestId);
        if (prior != null) {
            return prior.equals(key) ? GenerationAdmission.ADMITTED : GenerationAdmission.ERROR;
        }
        String reason;
        try {
            reason = load().generationRefusals().get(clientUserId);
        } catch (WebBlockException e) {
            return GenerationAdmission.UNAVAILABLE;
        }
        if (reason != null) {
            log.info("Prototype Payment stand-in refused a generation for project {}", projectId);
            return GenerationAdmission.refused(reason);
        }
        admittedGenerations.put(generationRequestId, key);
        log.info("Prototype Payment stand-in admitted a generation for project {}", projectId);
        return GenerationAdmission.ADMITTED;
    }

    private Loaded load() {
        if (file == null) {
            return new Loaded(0, 0, Map.of(), Map.of());
        }
        try {
            BasicFileAttributes attributes = Files.readAttributes(file, BasicFileAttributes.class);
            long modified = attributes.lastModifiedTime().toMillis();
            if (attributes.size() > MAX_FILE_BYTES) {
                throw new IllegalStateException("refusal file too large");
            }
            if (loaded == null || loaded.modified() != modified || loaded.size() != attributes.size()) {
                RefusalFile parsed = json.readValue(Files.readAllBytes(file), RefusalFile.class);
                Map<UUID, AdmissionRefusal> refusals = new ConcurrentHashMap<>();
                for (Refusal refusal :
                        parsed.websiteRefusals() == null ? List.<Refusal>of() : parsed.websiteRefusals()) {
                    if (refusal == null || refusal.clientUserId() == null || refusal.reason() == null) {
                        throw new IllegalStateException("invalid refusal entry");
                    }
                    refusals.put(refusal.clientUserId(), contractReason(refusal.reason()));
                }
                Map<UUID, String> generationRefusals = new ConcurrentHashMap<>();
                for (Refusal refusal :
                        parsed.generationRefusals() == null ? List.<Refusal>of() : parsed.generationRefusals()) {
                    if (refusal == null
                            || refusal.clientUserId() == null
                            || refusal.reason() == null
                            || !GENERATION_REASONS.contains(refusal.reason())) {
                        throw new IllegalStateException("invalid generation refusal entry");
                    }
                    generationRefusals.put(refusal.clientUserId(), refusal.reason());
                }
                loaded = new Loaded(modified, attributes.size(), Map.copyOf(refusals), Map.copyOf(generationRefusals));
            }
            return loaded;
        } catch (IOException | JacksonException | IllegalArgumentException | IllegalStateException e) {
            loaded = null;
            log.warn("Prototype Payment stand-in: the refusal file cannot be read or is invalid");
            throw new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE);
        }
    }

    /** One of the contract's reasons; OTHER stands for reasons Payment adds later and is not one. */
    private static AdmissionRefusal contractReason(String reason) {
        AdmissionRefusal refusal = AdmissionRefusal.valueOf(reason);
        if (refusal == AdmissionRefusal.OTHER) {
            throw new IllegalArgumentException("not a contract reason");
        }
        return refusal;
    }
}
