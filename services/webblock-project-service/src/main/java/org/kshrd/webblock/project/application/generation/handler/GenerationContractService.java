package org.kshrd.webblock.project.application.generation.handler;

import java.time.Clock;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.application.generation.config.GenerationProperties;
import org.kshrd.webblock.project.application.generation.model.ContractProductType;
import org.kshrd.webblock.project.application.generation.model.GenerationContractPage;
import org.kshrd.webblock.project.application.generation.model.GenerationContractRecord;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractSchemaEntity;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractRepository;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractSchemaRepository;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Generation contracts (spec 5.3.2): each freezes, per product type, its active schema with the highest version. The
 * catalog is another database, so it is read before the locked transaction (create) or after the database read (list),
 * never inside a transaction.
 */
@Service
public class GenerationContractService {

    private static final Comparator<ContractProductType> BY_PRODUCT_TYPE =
            FrozenSchemas.byProductTypeId(ContractProductType::productTypeId);

    private final ProjectService projects;
    private final CatalogReference catalog;
    private final GenerationContractRepository contracts;
    private final GenerationContractSchemaRepository contractSchemas;
    private final GenerationProperties properties;
    private final Clock clock;
    private final TransactionTemplate transaction;

    public GenerationContractService(
            ProjectService projects,
            CatalogReference catalog,
            GenerationContractRepository contracts,
            GenerationContractSchemaRepository contractSchemas,
            GenerationProperties properties,
            Clock clock,
            PlatformTransactionManager transactions) {
        this.projects = projects;
        this.catalog = catalog;
        this.contracts = contracts;
        this.contractSchemas = contractSchemas;
        this.properties = properties;
        this.clock = clock;
        // Pinned: the version read after the lock wait must see what the previous lock holder committed.
        this.transaction = new TransactionTemplate(transactions);
        this.transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    /**
     * Freezes the active schemas of these product types as the project's next contract version. productTypeIds holds at
     * least one ID and no duplicates (the request checks it).
     */
    public GenerationContractRecord create(UUID projectId, UUID callerUserId, List<UUID> productTypeIds) {
        ProjectRecord project = projects.getOwned(projectId, callerUserId);
        refuseReadOnly(project);
        Map<UUID, ProductTypeSchema> schemas =
                catalog.activeProductTypeSchemas(project.businessTypeId(), productTypeIds);
        List<ErrorDetail> notAllowed = new ArrayList<>();
        for (int i = 0; i < productTypeIds.size(); i++) {
            if (!schemas.containsKey(productTypeIds.get(i))) {
                notAllowed.add(ErrorDetail.of("productTypeIds[" + i + "]", ErrorCode.PRODUCT_TYPE_NOT_ALLOWED));
            }
        }
        if (!notAllowed.isEmpty()) {
            throw new WebBlockException(ErrorCode.PRODUCT_TYPE_NOT_ALLOWED, notAllowed);
        }
        GenerationContractEntity saved =
                transaction.execute(status -> freeze(projectId, callerUserId, schemas.values()));
        return toRecord(
                saved,
                schemas.values().stream()
                        .map(s -> new ContractProductType(s.productTypeId(), s.versionNumber()))
                        .toList());
    }

    /** Newest first. schemaVersion is read from the catalog, after the database read and outside its transaction. */
    public GenerationContractPage list(UUID projectId, UUID callerUserId, int limit, String cursor) {
        if (limit < 1 || limit > ProjectService.MAX_LIST_LIMIT) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("limit", ErrorCode.FIELD_OUT_OF_RANGE)));
        }
        ContractCursor after = cursor == null ? null : ContractCursor.decode(cursor);
        projects.getOwned(projectId, callerUserId);
        Limit fetch = Limit.of(limit + 1);
        List<GenerationContractEntity> rows = after == null
                ? contracts.firstPage(projectId, fetch)
                : contracts.pageAfter(projectId, after.versionNumber(), fetch);
        boolean more = rows.size() > limit;
        List<GenerationContractEntity> page = more ? rows.subList(0, limit) : rows;
        if (page.isEmpty()) {
            return new GenerationContractPage(List.of(), null);
        }
        Map<UUID, List<GenerationContractSchemaEntity>> frozen = contractSchemas
                .findByGenerationContractIdIn(page.stream()
                        .map(GenerationContractEntity::generationContractId)
                        .toList())
                .stream()
                .collect(Collectors.groupingBy(GenerationContractSchemaEntity::generationContractId));
        Map<UUID, ProductTypeSchema> schemas = catalog.productTypeSchemas(frozen.values().stream()
                .flatMap(List::stream)
                .map(GenerationContractSchemaEntity::productTypeSchemaId)
                .collect(Collectors.toSet()));
        List<GenerationContractRecord> items = page.stream()
                .map(contract -> toRecord(
                        contract,
                        frozen.getOrDefault(contract.generationContractId(), List.of()).stream()
                                .map(row -> new ContractProductType(
                                        row.productTypeId(), schemaVersion(schemas, row.productTypeSchemaId())))
                                .toList()))
                .toList();
        String next = more ? new ContractCursor(page.getLast().versionNumber()).encode() : null;
        return new GenerationContractPage(items, next);
    }

    /** The locked part of create: lockOwned is the first read of the project in this transaction. */
    private GenerationContractEntity freeze(UUID projectId, UUID callerUserId, Collection<ProductTypeSchema> schemas) {
        // The project may have become read-only during the catalog read.
        refuseReadOnly(projects.lockOwned(projectId, callerUserId));
        GenerationContractEntity contract = contracts.saveAndFlush(GenerationContractEntity.create(
                Uuids.v7(),
                projectId,
                contracts.maxVersion(projectId) + 1,
                properties.apiContractVersion(),
                callerUserId,
                clock.instant().truncatedTo(ChronoUnit.MICROS)));
        contractSchemas.saveAllAndFlush(schemas.stream()
                .map(s -> GenerationContractSchemaEntity.create(
                        contract.generationContractId(), s.productTypeSchemaId(), s.productTypeId()))
                .toList());
        return contract;
    }

    private static void refuseReadOnly(ProjectRecord project) {
        if (project.readOnly()) {
            throw new WebBlockException(ErrorCode.PROJECT_READ_ONLY);
        }
    }

    /**
     * Catalog schema rows are versioned and never rewritten (catalog V2): a frozen schema missing from the catalog is a
     * broken invariant, answered as a 500, not a client error.
     */
    private static int schemaVersion(Map<UUID, ProductTypeSchema> schemas, UUID productTypeSchemaId) {
        return FrozenSchemas.require(schemas, productTypeSchemaId).versionNumber();
    }

    private static GenerationContractRecord toRecord(GenerationContractEntity e, List<ContractProductType> types) {
        return new GenerationContractRecord(
                e.generationContractId(),
                e.versionNumber(),
                e.apiContractVersion(),
                types.stream().sorted(BY_PRODUCT_TYPE).toList(),
                e.createdByUserId(),
                e.createdAt());
    }
}
