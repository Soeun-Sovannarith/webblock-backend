package org.kshrd.webblock.project.application.generation.handler;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.application.generation.model.CodegenInput;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationContractSchemaEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestEntity;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractRepository;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractSchemaRepository;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationRequestRepository;
import org.springframework.stereotype.Service;

/**
 * The input AI Codegen reads for a generating request. The project database is read first; the catalog is another
 * database and is read after it, with no transaction open (each repository call is its own short one).
 */
@Service
public class CodegenInputService {

    private static final Comparator<CodegenInput.ProductType> BY_PRODUCT_TYPE =
            FrozenSchemas.byProductTypeId(CodegenInput.ProductType::productTypeId);

    private final GenerationRequestRepository requests;
    private final GenerationContractRepository contracts;
    private final GenerationContractSchemaRepository contractSchemas;
    private final ProjectService projects;
    private final CatalogReference catalog;

    public CodegenInputService(
            GenerationRequestRepository requests,
            GenerationContractRepository contracts,
            GenerationContractSchemaRepository contractSchemas,
            ProjectService projects,
            CatalogReference catalog) {
        this.requests = requests;
        this.contracts = contracts;
        this.contractSchemas = contractSchemas;
        this.projects = projects;
        this.catalog = catalog;
    }

    /**
     * @throws WebBlockException GENERATION_REQUEST_NOT_FOUND, REQUEST_NOT_GENERATING, or CATALOG_UNAVAILABLE (J2-22)
     * @throws IllegalStateException a row the schema guarantees is missing: answered as a 500
     */
    public CodegenInput codegenInput(UUID generationRequestId) {
        GenerationRequestEntity request = requests.findById(generationRequestId)
                .orElseThrow(() -> new WebBlockException(ErrorCode.GENERATION_REQUEST_NOT_FOUND));
        ProjectRecord project = projects.find(request.projectId())
                .orElseThrow(() -> new IllegalStateException("Project " + request.projectId() + " is missing"));
        if (project.deleted()) {
            // A deleted website is unknown: its captured content is not served, whatever the request's status.
            throw new WebBlockException(ErrorCode.GENERATION_REQUEST_NOT_FOUND);
        }
        if (request.status() != GenerationRequestStatus.generating) {
            throw new WebBlockException(ErrorCode.REQUEST_NOT_GENERATING);
        }
        GenerationContractEntity contract = contracts
                .findById(request.generationContractId())
                .orElseThrow(
                        () -> new IllegalStateException("Contract " + request.generationContractId() + " is missing"));
        List<GenerationContractSchemaEntity> frozen =
                contractSchemas.findByGenerationContractIdIn(List.of(contract.generationContractId()));

        // Catalog reads, no transaction open.
        Map<UUID, ProductTypeSchema> schemas = catalog.productTypeSchemas(frozen.stream()
                .map(GenerationContractSchemaEntity::productTypeSchemaId)
                .toList());
        BusinessType businessType = catalog.businessType(project.businessTypeId())
                .orElseThrow(() -> new IllegalStateException(
                        "Business type " + project.businessTypeId() + " is not in the catalog"));

        List<CodegenInput.ProductType> productTypes = frozen.stream()
                .map(row -> productType(row, schemas))
                .sorted(BY_PRODUCT_TYPE)
                .toList();
        return new CodegenInput(
                request.generationRequestId(),
                request.projectId(),
                businessType.code(),
                request.capturedInput(),
                request.capturedInputHash(),
                new CodegenInput.Contract(
                        contract.generationContractId(), contract.apiContractVersion(), productTypes));
    }

    /** A frozen schema missing from the catalog is a broken invariant (catalog schemas are never rewritten). */
    private static CodegenInput.ProductType productType(
            GenerationContractSchemaEntity row, Map<UUID, ProductTypeSchema> schemas) {
        ProductTypeSchema schema = FrozenSchemas.require(schemas, row.productTypeSchemaId());
        return new CodegenInput.ProductType(
                row.productTypeId(), schema.code(), schema.productTypeSchemaId(), schema.fieldSchema());
    }
}
