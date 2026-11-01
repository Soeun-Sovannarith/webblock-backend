package org.kshrd.webblock.project.application.generation.handler;

import io.namastack.outbox.Outbox;
import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.application.generation.job.GenerationAdmissionJob;
import org.kshrd.webblock.project.application.generation.model.CaptureOutcome;
import org.kshrd.webblock.project.application.generation.model.DeployVersion;
import org.kshrd.webblock.project.application.generation.model.DeployVersionPage;
import org.kshrd.webblock.project.application.generation.model.GenerationRequestPage;
import org.kshrd.webblock.project.application.generation.model.GenerationRequestRecord;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractSchemaEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractRepository;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractSchemaRepository;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationRequestRepository;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Generation requests (spec 5.3.3): the owner captures one, idempotent on its Idempotency-Key, and reads them back.
 * Capture is one transaction under the project row lock, so the snapshot is one edit revision and at most one request
 * per project is in flight; Payment is asked later, by the admission job it schedules.
 */
@Service
public class GenerationRequestService {

    private static final int CONTRACTS_LOOKED_AT = 50;

    private final ProjectService projects;
    private final PageService pages;
    private final GenerationContractRepository contracts;
    private final GenerationContractSchemaRepository contractSchemas;
    private final GenerationContractService contractService;
    private final CatalogReference catalog;
    private final GenerationRequestRepository requests;
    private final Outbox outbox;
    private final JsonMapper json;
    private final Clock clock;

    public GenerationRequestService(
            ProjectService projects,
            PageService pages,
            GenerationContractRepository contracts,
            GenerationContractSchemaRepository contractSchemas,
            GenerationContractService contractService,
            CatalogReference catalog,
            GenerationRequestRepository requests,
            Outbox outbox,
            JsonMapper json,
            Clock clock) {
        this.projects = projects;
        this.pages = pages;
        this.contracts = contracts;
        this.contractSchemas = contractSchemas;
        this.contractService = contractService;
        this.catalog = catalog;
        this.requests = requests;
        this.outbox = outbox;
        this.json = json;
        this.clock = clock;
    }

    /**
     * Captures a request, or answers the one this key already captured with its status now (created false). Pinned to
     * READ COMMITTED: what is read after the lock wait must include what the previous lock holder committed.
     *
     * @throws WebBlockException PROJECT_NOT_FOUND, IDEMPOTENCY_KEY_REUSED, PROJECT_READ_ONLY or
     *     GENERATION_CONTRACT_NOT_FOUND; a request already in flight fails the insert on
     *     generation_requests_one_in_flight, which GenerationConfig maps to GENERATION_IN_PROGRESS
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CaptureOutcome capture(UUID projectId, UUID callerUserId, String idempotencyKey, UUID requestedContractId) {
        // The first read of the project in this transaction: findForUpdate does not refresh a loaded entity.
        ProjectRecord project = projects.lockOwned(projectId, callerUserId);
        // The hash is of the body as sent: a replay without a contract ID matches, whatever contract the first call
        // made.
        String requestHash = InputHasher.sha256(json.writeValueAsString(
                requestedContractId == null
                        ? Map.of()
                        : Map.of("generationContractId", requestedContractId.toString())));
        // Before the read-only check (J2-19): a known key still answers after the project became read-only.
        Optional<GenerationRequestEntity> known = requests.findByProjectIdAndIdempotencyKey(projectId, idempotencyKey);
        if (known.isPresent()) {
            if (!known.get().requestHash().equals(requestHash)) {
                throw new WebBlockException(ErrorCode.IDEMPOTENCY_KEY_REUSED);
            }
            return new CaptureOutcome(
                    known.get().generationRequestId(), known.get().status(), false);
        }
        if (project.readOnly()) {
            throw new WebBlockException(ErrorCode.PROJECT_READ_ONLY);
        }
        UUID generationContractId;
        if (requestedContractId == null) {
            generationContractId = contractOfAllActiveTypes(project, callerUserId);
        } else if (contracts.existsByProjectIdAndGenerationContractId(projectId, requestedContractId)) {
            generationContractId = requestedContractId;
        } else {
            throw new WebBlockException(ErrorCode.GENERATION_CONTRACT_NOT_FOUND);
        }
        String input =
                json.writeValueAsString(capturedInput(project, generationContractId, pages.livePages(projectId)));
        GenerationRequestEntity request = requests.saveAndFlush(GenerationRequestEntity.captured(
                Uuids.v7(),
                projectId,
                idempotencyKey,
                requestHash,
                generationContractId,
                project.editRevision(),
                input,
                InputHasher.sha256(input),
                now()));
        GenerationAdmissionJob job = new GenerationAdmissionJob(request.generationRequestId());
        outbox.schedule(job, job.outboxKey());
        return new CaptureOutcome(request.generationRequestId(), GenerationRequestStatus.captured, true);
    }

    /**
     * The project's newest contract that froze exactly the current active schema of every active product type of its
     * business type, else a new one made the same way as FR-22 (same checks, same rows, this transaction). The catalog
     * is read here, under the project lock, bounded by its statement timeout.
     */
    private UUID contractOfAllActiveTypes(ProjectRecord project, UUID callerUserId) {
        Map<UUID, ProductTypeSchema> active = catalog.allActiveProductTypeSchemas(project.businessTypeId());
        if (active.isEmpty()) {
            throw new WebBlockException(ErrorCode.PRODUCT_TYPE_NOT_ALLOWED);
        }
        Set<UUID> wanted = active.values().stream()
                .map(ProductTypeSchema::productTypeSchemaId)
                .collect(Collectors.toSet());
        List<GenerationContractEntity> newest = contracts.firstPage(project.projectId(), Limit.of(CONTRACTS_LOOKED_AT));
        Map<UUID, Set<UUID>> frozen = contractSchemas
                .findByGenerationContractIdIn(newest.stream()
                        .map(GenerationContractEntity::generationContractId)
                        .toList())
                .stream()
                .collect(Collectors.groupingBy(
                        GenerationContractSchemaEntity::generationContractId,
                        Collectors.mapping(GenerationContractSchemaEntity::productTypeSchemaId, Collectors.toSet())));
        for (GenerationContractEntity contract : newest) {
            if (wanted.equals(frozen.get(contract.generationContractId()))) {
                return contract.generationContractId();
            }
        }
        return contractService
                .create(project.projectId(), callerUserId, List.copyOf(active.keySet()))
                .generationContractId();
    }

    /** Whether any request of the project reached {@code published}; no owner check, the caller has made it. */
    @Transactional(readOnly = true)
    public boolean hasPublishedRequest(UUID projectId) {
        return requests.existsByProjectIdAndStatus(projectId, GenerationRequestStatus.published);
    }

    /** Whether a request of the project is captured, generating or publishing; no owner check. */
    @Transactional(readOnly = true)
    public boolean hasRequestInFlight(UUID projectId) {
        return requests.existsByProjectIdAndStatusIn(
                projectId,
                java.util.EnumSet.of(
                        GenerationRequestStatus.captured,
                        GenerationRequestStatus.generating,
                        GenerationRequestStatus.publishing));
    }

    /** Newest first, in cursor pages (J2-16). */
    /**
     * For each project that has one, the edit revision of its last deployed code: the highest captured edit revision
     * among its published requests, in one query. A project with no published request is absent from the map. No
     * owner check: the caller passes projects it already read as the owner's.
     */
    @Transactional(readOnly = true)
    public Map<UUID, Long> lastDeployedEditRevisions(Collection<UUID> projectIds) {
        if (projectIds.isEmpty()) {
            return Map.of();
        }
        Map<UUID, Long> revisions = new HashMap<>();
        for (GenerationRequestRepository.DeployedRevision row :
                requests.deployedRevisions(projectIds, GenerationRequestStatus.published)) {
            revisions.put(row.getProjectId(), row.getRevision());
        }
        return revisions;
    }

    @Transactional(readOnly = true)
    public GenerationRequestPage list(UUID projectId, UUID callerUserId, int limit, String cursor) {
        if (limit < 1 || limit > ProjectService.MAX_LIST_LIMIT) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("limit", ErrorCode.FIELD_OUT_OF_RANGE)));
        }
        RequestCursor after = cursor == null ? null : RequestCursor.decode(cursor);
        projects.getOwned(projectId, callerUserId);
        Limit fetch = Limit.of(limit + 1);
        List<GenerationRequestEntity> rows = after == null
                ? requests.firstPage(projectId, fetch)
                : requests.pageAfter(projectId, after.createdAt(), after.generationRequestId(), fetch);
        boolean more = rows.size() > limit;
        List<GenerationRequestEntity> page = more ? rows.subList(0, limit) : rows;
        String next = more
                ? new RequestCursor(page.getLast().createdAt(), page.getLast().generationRequestId()).encode()
                : null;
        return new GenerationRequestPage(
                page.stream().map(GenerationRequestService::toRecord).toList(), next);
    }

    /**
     * The project's deploys (published requests), newest first by the time they reached published (updated_at), in
     * cursor pages. No owner check: the caller has made it. The newest deploy of the project is {@code current}.
     */
    @Transactional(readOnly = true)
    public DeployVersionPage versions(UUID projectId, int limit, String cursor) {
        if (limit < 1 || limit > ProjectService.MAX_LIST_LIMIT) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("limit", ErrorCode.FIELD_OUT_OF_RANGE)));
        }
        RequestCursor after = cursor == null ? null : RequestCursor.decode(cursor);
        Limit fetch = Limit.of(limit + 1);
        GenerationRequestStatus published = GenerationRequestStatus.published;
        List<GenerationRequestEntity> rows = after == null
                ? requests.publishedFirstPage(projectId, published, fetch)
                : requests.publishedPageAfter(
                        projectId, published, after.createdAt(), after.generationRequestId(), fetch);
        boolean more = rows.size() > limit;
        List<GenerationRequestEntity> page = more ? rows.subList(0, limit) : rows;
        String next = more
                ? new RequestCursor(page.getLast().updatedAt(), page.getLast().generationRequestId()).encode()
                : null;
        return new DeployVersionPage(
                page.stream()
                        .map(e -> new DeployVersion(
                                e.generationRequestId(),
                                e.capturedEditRevision(),
                                e.updatedAt(),
                                !requests.existsPublishedAfter(
                                        projectId, published, e.updatedAt(), e.generationRequestId())))
                        .toList(),
                next);
    }

    /**
     * The frozen input of one of the project's deploys, as JSON text; a request that is unknown, of another project or
     * not published is VERSION_NOT_FOUND. No owner check: the caller has made it.
     */
    @Transactional(readOnly = true)
    public String versionInput(UUID projectId, UUID versionId) {
        return requests.findByProjectIdAndGenerationRequestId(projectId, versionId)
                .filter(e -> e.status() == GenerationRequestStatus.published)
                .map(GenerationRequestEntity::capturedInput)
                .orElseThrow(() -> new WebBlockException(ErrorCode.VERSION_NOT_FOUND));
    }

    @Transactional(readOnly = true)
    public GenerationRequestRecord get(UUID projectId, UUID callerUserId, UUID generationRequestId) {
        projects.getOwned(projectId, callerUserId);
        return requests.findByProjectIdAndGenerationRequestId(projectId, generationRequestId)
                .map(GenerationRequestService::toRecord)
                .orElseThrow(() -> new WebBlockException(ErrorCode.GENERATION_REQUEST_NOT_FOUND));
    }

    /** Spec 5.3.3 step 6; the pages come in page order. A missing SEO text is written as JSON null. */
    private ObjectNode capturedInput(ProjectRecord project, UUID generationContractId, List<PageRecord> live) {
        ObjectNode input = json.createObjectNode();
        input.put("componentCatalogVersion", project.componentCatalogVersion());
        input.put("generationContractId", generationContractId.toString());
        ArrayNode list = input.putArray("pages");
        for (PageRecord page : live) {
            list.addObject()
                    .put("pageId", page.pageId().toString())
                    .put("path", page.path())
                    .put("name", page.name())
                    .put("sortOrder", page.sortOrder())
                    .put("seoTitle", page.seoTitle())
                    .put("seoDescription", page.seoDescription())
                    .set("puckTree", page.puckTree());
        }
        return input;
    }

    private static GenerationRequestRecord toRecord(GenerationRequestEntity e) {
        return new GenerationRequestRecord(
                e.generationRequestId(), e.status(), e.failureCode(), e.createdAt(), e.updatedAt());
    }

    private Instant now() {
        return clock.instant().truncatedTo(ChronoUnit.MICROS);
    }
}
