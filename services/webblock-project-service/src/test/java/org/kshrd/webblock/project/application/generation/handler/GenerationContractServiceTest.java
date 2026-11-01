package org.kshrd.webblock.project.application.generation.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;
import org.assertj.core.api.ThrowableAssert.ThrowingCallable;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.ProductTypeSchema;
import org.kshrd.webblock.project.application.generation.model.ContractProductType;
import org.kshrd.webblock.project.application.generation.model.GenerationContractPage;
import org.kshrd.webblock.project.application.generation.model.GenerationContractRecord;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.infrastructure.persistence.generation.GenerationContractRepository;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.node.JsonNodeFactory;

/** Generation contracts on real PostgreSQL 18, the catalog stubbed per test. */
@ServiceIntegrationTest
class GenerationContractServiceTest {

    @Autowired
    GenerationContractService contracts;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    GenerationContractRepository repository;

    @MockitoBean
    CatalogReference catalog;

    private ProjectRecord project() {
        UUID id = Uuids.v7();
        return tx.execute(s -> projects.create(
                new NewProject(id, Uuids.v7(), "Contracts", TestSubdomains.of(id), UUID.randomUUID(), "0.1.0")));
    }

    private static ProductTypeSchema schema(UUID productTypeId, UUID schemaId, int version) {
        return new ProductTypeSchema(
                productTypeId, "type-" + version, schemaId, version, JsonNodeFactory.instance.objectNode());
    }

    /** The catalog answers these schemas, keyed by product type for the freeze and by schema ID for the list. */
    private void catalogHas(ProjectRecord p, ProductTypeSchema... schemas) {
        Map<UUID, ProductTypeSchema> byType = new LinkedHashMap<>();
        Map<UUID, ProductTypeSchema> bySchema = new LinkedHashMap<>();
        for (ProductTypeSchema s : schemas) {
            byType.put(s.productTypeId(), s);
            bySchema.put(s.productTypeSchemaId(), s);
        }
        when(catalog.activeProductTypeSchemas(eq(p.businessTypeId()), anyCollection()))
                .thenAnswer(call -> only(byType, call.getArgument(1)));
        when(catalog.productTypeSchemas(anyCollection())).thenAnswer(call -> only(bySchema, call.getArgument(0)));
    }

    private static Map<UUID, ProductTypeSchema> only(Map<UUID, ProductTypeSchema> known, Collection<UUID> asked) {
        Map<UUID, ProductTypeSchema> result = new LinkedHashMap<>();
        asked.stream().filter(known::containsKey).forEach(id -> result.put(id, known.get(id)));
        return result;
    }

    private int contractRows(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT count(*) FROM project.generation_contracts WHERE project_id = ?", Integer.class, projectId);
    }

    private int schemaRows(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT count(*) FROM project.generation_contract_schemas s JOIN project.generation_contracts c"
                        + " ON c.generation_contract_id = s.generation_contract_id WHERE c.project_id = ?",
                Integer.class,
                projectId);
    }

    private static void assertCode(ThrowingCallable call, ErrorCode code) {
        assertThatThrownBy(call)
                .isInstanceOfSatisfying(
                        WebBlockException.class, e -> assertThat(e.errorCode()).isEqualTo(code));
    }

    @Test
    void freezesTheHighestActiveSchemaOfEachType() {
        ProjectRecord p = project();
        UUID t1 = UUID.fromString("10000000-0000-4000-8000-000000000001");
        UUID t2 = UUID.fromString("20000000-0000-4000-8000-000000000002");
        UUID s1 = Uuids.v7();
        UUID s2 = Uuids.v7();
        catalogHas(p, schema(t1, s1, 2), schema(t2, s2, 1));

        GenerationContractRecord record = contracts.create(p.projectId(), p.ownerUserId(), List.of(t2, t1));

        assertThat(record.versionNumber()).isEqualTo(1);
        assertThat(record.apiContractVersion()).isEqualTo("1.0.0");
        assertThat(record.productTypes())
                .containsExactly(new ContractProductType(t1, 2), new ContractProductType(t2, 1));
        assertThat(record.createdByUserId()).isEqualTo(p.ownerUserId());
        assertThat(record.createdAt()).isNotNull();
        assertThat(contractRows(p.projectId())).isEqualTo(1);
        assertThat(jdbc.queryForList(
                        "SELECT product_type_id::text || '/' || product_type_schema_id::text"
                                + " FROM project.generation_contract_schemas WHERE generation_contract_id = ?",
                        String.class,
                        record.generationContractId()))
                .containsExactlyInAnyOrder(t1 + "/" + s1, t2 + "/" + s2);
        assertThat(jdbc.queryForObject(
                        "SELECT api_contract_version FROM project.generation_contracts WHERE generation_contract_id = ?",
                        String.class,
                        record.generationContractId()))
                .isEqualTo("1.0.0");
        assertThat(repository.existsByProjectIdAndGenerationContractId(p.projectId(), record.generationContractId()))
                .isTrue();
        assertThat(repository.existsByProjectIdAndGenerationContractId(
                        project().projectId(), record.generationContractId()))
                .isFalse();
    }

    /** Lead ruling C2: productTypeId ascending as its text (PostgreSQL's uuid order), not UUID.compareTo (signed). */
    @Test
    void productTypesAreSortedByProductTypeIdInTheAnswerAndTheList() {
        ProjectRecord p = project();
        UUID high = UUID.fromString("f0000000-0000-4000-8000-000000000000");
        UUID low = UUID.fromString("10000000-0000-4000-8000-000000000000");
        UUID middle = UUID.fromString("80000000-0000-4000-8000-000000000000");
        catalogHas(p, schema(high, Uuids.v7(), 1), schema(low, Uuids.v7(), 1), schema(middle, Uuids.v7(), 1));

        GenerationContractRecord created = contracts.create(p.projectId(), p.ownerUserId(), List.of(high, low, middle));
        GenerationContractRecord listed =
                contracts.list(p.projectId(), p.ownerUserId(), 20, null).items().getFirst();

        assertThat(created.productTypes())
                .extracting(ContractProductType::productTypeId)
                .containsExactly(low, middle, high);
        assertThat(listed.productTypes()).isEqualTo(created.productTypes());
    }

    @Test
    void versionsFollowWithoutGaps() {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        catalogHas(p, schema(t, Uuids.v7(), 1));
        List<Integer> versions = IntStream.range(0, 3)
                .mapToObj(i -> contracts
                        .create(p.projectId(), p.ownerUserId(), List.of(t))
                        .versionNumber())
                .toList();
        assertThat(versions).containsExactly(1, 2, 3);
    }

    @Test
    void concurrentCreatesGetConsecutiveVersions() throws Exception {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        catalogHas(p, schema(t, Uuids.v7(), 1));
        int threads = 10;
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            List<Future<Integer>> results = new ArrayList<>();
            for (int i = 0; i < threads; i++) {
                Callable<Integer> create = () -> {
                    start.await();
                    return contracts
                            .create(p.projectId(), p.ownerUserId(), List.of(t))
                            .versionNumber();
                };
                results.add(pool.submit(create));
            }
            start.countDown();
            List<Integer> versions = new ArrayList<>();
            for (Future<Integer> result : results) {
                versions.add(result.get(60, TimeUnit.SECONDS));
            }
            assertThat(versions)
                    .containsExactlyInAnyOrderElementsOf(
                            IntStream.rangeClosed(1, threads).boxed().toList());
        } finally {
            pool.shutdownNow();
        }
        assertThat(contractRows(p.projectId())).isEqualTo(threads);
    }

    /** Review Focus 4: only the invalid indices are marked, and nothing is stored. */
    @Test
    void marksOnlyTheInvalidIndices() {
        ProjectRecord p = project();
        UUID ok1 = Uuids.v7();
        UUID ok2 = Uuids.v7();
        catalogHas(p, schema(ok1, Uuids.v7(), 1), schema(ok2, Uuids.v7(), 3));

        assertThatThrownBy(() -> contracts.create(
                        p.projectId(), p.ownerUserId(), List.of(ok1, UUID.randomUUID(), ok2, UUID.randomUUID())))
                .isInstanceOfSatisfying(WebBlockException.class, e -> {
                    assertThat(e.errorCode()).isEqualTo(ErrorCode.PRODUCT_TYPE_NOT_ALLOWED);
                    assertThat(e.details())
                            .extracting(ErrorDetail::field)
                            .containsExactly("productTypeIds[1]", "productTypeIds[3]");
                    assertThat(e.details())
                            .extracting(ErrorDetail::code)
                            .containsOnly(ErrorCode.PRODUCT_TYPE_NOT_ALLOWED.code());
                });
        assertThat(contractRows(p.projectId())).isZero();
        assertThat(schemaRows(p.projectId())).isZero();
    }

    @Test
    void aReadOnlyProjectRefusesAndKeepsItsContractsReadable() {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        catalogHas(p, schema(t, Uuids.v7(), 1));
        contracts.create(p.projectId(), p.ownerUserId(), List.of(t));
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", p.projectId());

        assertCode(() -> contracts.create(p.projectId(), p.ownerUserId(), List.of(t)), ErrorCode.PROJECT_READ_ONLY);
        assertThat(contracts.list(p.projectId(), p.ownerUserId(), 20, null).items())
                .extracting(GenerationContractRecord::versionNumber)
                .containsExactly(1);
        assertThat(contractRows(p.projectId())).isEqualTo(1);
    }

    /** The read-only state is checked again under the project lock: it may change during the catalog read. */
    @Test
    @org.junit.jupiter.api.DisplayName("FR-38.3: a project deleted during the catalog read is not found under the lock")
    void aProjectDeletedDuringTheCatalogReadIsNotFoundUnderTheLock() {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        ProductTypeSchema s = schema(t, Uuids.v7(), 1);
        when(catalog.activeProductTypeSchemas(eq(p.businessTypeId()), anyCollection()))
                .thenAnswer(call -> {
                    jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", p.projectId());
                    return Map.of(t, s);
                });

        assertCode(() -> contracts.create(p.projectId(), p.ownerUserId(), List.of(t)), ErrorCode.PROJECT_NOT_FOUND);
        assertThat(contractRows(p.projectId())).isZero();
    }

    @Test
    void anotherOwnerGetsProjectNotFound() {
        ProjectRecord p = project();
        UUID stranger = Uuids.v7();
        assertCode(() -> contracts.create(p.projectId(), stranger, List.of(Uuids.v7())), ErrorCode.PROJECT_NOT_FOUND);
        assertCode(() -> contracts.list(p.projectId(), stranger, 20, null), ErrorCode.PROJECT_NOT_FOUND);
        verifyNoInteractions(catalog);
    }

    @Test
    void aBadCursorOrLimitIsAnsweredBeforeTheOwnerCheck() {
        ProjectRecord p = project();
        UUID stranger = Uuids.v7();
        assertCode(() -> contracts.list(p.projectId(), stranger, 20, "not base64!"), ErrorCode.VALIDATION_FAILED);
        assertCode(() -> contracts.list(p.projectId(), stranger, 0, null), ErrorCode.VALIDATION_FAILED);
    }

    @Test
    void aCatalogOutageIsCatalogUnavailableAndStoresNothing() {
        ProjectRecord p = project();
        when(catalog.activeProductTypeSchemas(any(), anyCollection()))
                .thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));
        assertCode(
                () -> contracts.create(p.projectId(), p.ownerUserId(), List.of(Uuids.v7())),
                ErrorCode.CATALOG_UNAVAILABLE);
        assertThat(contractRows(p.projectId())).isZero();
    }

    /** J2-24: schemaVersion is read through productTypeSchemas, once per page. */
    @Test
    void listsNewestFirstInCursorPages() {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        catalogHas(p, schema(t, Uuids.v7(), 4));
        for (int i = 0; i < 3; i++) {
            contracts.create(p.projectId(), p.ownerUserId(), List.of(t));
        }

        GenerationContractPage first = contracts.list(p.projectId(), p.ownerUserId(), 2, null);
        assertThat(first.items())
                .extracting(GenerationContractRecord::versionNumber)
                .containsExactly(3, 2);
        assertThat(first.items())
                .allSatisfy(c -> assertThat(c.productTypes()).containsExactly(new ContractProductType(t, 4)));
        assertThat(first.nextCursor()).isNotNull();

        GenerationContractPage second = contracts.list(p.projectId(), p.ownerUserId(), 2, first.nextCursor());
        assertThat(second.items())
                .extracting(GenerationContractRecord::versionNumber)
                .containsExactly(1);
        assertThat(second.items().getFirst().createdByUserId()).isEqualTo(p.ownerUserId());
        assertThat(second.nextCursor()).isNull();

        verify(catalog, times(2)).productTypeSchemas(anyCollection());
    }

    @Test
    void aProjectWithoutContractsListsNothingAndAsksTheCatalogNothing() {
        ProjectRecord p = project();
        GenerationContractPage page = contracts.list(p.projectId(), p.ownerUserId(), 20, null);
        assertThat(page.items()).isEmpty();
        assertThat(page.nextCursor()).isNull();
        verify(catalog, never()).productTypeSchemas(anyCollection());
    }

    @Test
    void aBadCursorOrLimitIsAValidationError() {
        ProjectRecord p = project();
        for (String cursor : List.of("", "not base64!", "MA", "LTE", "MDE", "eA")) {
            assertThatThrownBy(() -> contracts.list(p.projectId(), p.ownerUserId(), 20, cursor))
                    .as(cursor)
                    .isInstanceOfSatisfying(WebBlockException.class, e -> {
                        assertThat(e.errorCode()).isEqualTo(ErrorCode.VALIDATION_FAILED);
                        assertThat(e.details()).extracting(ErrorDetail::field).containsExactly("cursor");
                    });
        }
        for (int limit : new int[] {0, ProjectService.MAX_LIST_LIMIT + 1}) {
            assertThatThrownBy(() -> contracts.list(p.projectId(), p.ownerUserId(), limit, null))
                    .isInstanceOfSatisfying(
                            WebBlockException.class,
                            e -> assertThat(e.details())
                                    .extracting(ErrorDetail::field)
                                    .containsExactly("limit"));
        }
    }

    @Test
    void listingWithoutTheCatalogIsCatalogUnavailable() {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        catalogHas(p, schema(t, Uuids.v7(), 1));
        contracts.create(p.projectId(), p.ownerUserId(), List.of(t));
        when(catalog.productTypeSchemas(anyCollection()))
                .thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));
        assertCode(() -> contracts.list(p.projectId(), p.ownerUserId(), 20, null), ErrorCode.CATALOG_UNAVAILABLE);
    }

    /** Not chosen by the brief: a frozen schema the catalog no longer has is a broken invariant (a 500), not a 4xx. */
    @Test
    void aFrozenSchemaMissingFromTheCatalogIsAnError() {
        ProjectRecord p = project();
        UUID t = Uuids.v7();
        catalogHas(p, schema(t, Uuids.v7(), 1));
        contracts.create(p.projectId(), p.ownerUserId(), List.of(t));
        when(catalog.productTypeSchemas(anyCollection())).thenReturn(Map.of());
        assertThatThrownBy(() -> contracts.list(p.projectId(), p.ownerUserId(), 20, null))
                .isInstanceOf(IllegalStateException.class);
    }
}
