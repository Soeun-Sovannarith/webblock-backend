package org.kshrd.webblock.project.application.wizard.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Consumer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.job.CatalogRegistrationScheduler;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.payment.handler.AdmissionReleaseFence;
import org.kshrd.webblock.project.application.payment.model.AdmissionDecision;
import org.kshrd.webblock.project.application.payment.model.AdmissionRefusal;
import org.kshrd.webblock.project.application.payment.model.WebsiteAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.template.handler.TemplateCatalog;
import org.kshrd.webblock.project.application.template.model.SiteTemplate;
import org.kshrd.webblock.project.application.template.model.TemplatePage;
import org.kshrd.webblock.project.application.wizard.model.CreateProjectCommand;
import org.kshrd.webblock.project.application.wizard.model.CreationOutcome;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.mockito.InOrder;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import tools.jackson.databind.json.JsonMapper;

class ProjectCreationServiceTest {

    static final UUID OWNER = UUID.randomUUID();
    static final UUID PROJECT = UUID.randomUUID();
    static final BusinessType TYPE = new BusinessType(UUID.randomUUID(), "clothing_shop", "Clothing shop", true);
    static final SiteTemplate TEMPLATE = new SiteTemplate(
            "blank",
            "Blank",
            null,
            null,
            null,
            null,
            "1.0.0",
            List.of("*"),
            "0.1.0",
            List.of(new TemplatePage(
                    "Home", "/", 0, null, null, JsonMapper.builder().build().readTree("{}"))));
    static final CreateProjectCommand COMMAND = new CreateProjectCommand(
            "My shop",
            "kilncoffee",
            "clothing_shop",
            "blank",
            new BusinessProfile("My shop", null, null, null, null, "UTC"));

    static final AdmissionReleaseFence.State NO_RELEASE = new AdmissionReleaseFence.State(0, 0);

    /**
     * Runs the callback with no real transaction (the code under test only needs the transaction contract) and records
     * the isolation level each transaction asked for.
     */
    static final class NoTransactions extends AbstractPlatformTransactionManager {
        final List<Integer> isolationLevels = new java.util.ArrayList<>();

        @Override
        protected Object doGetTransaction() {
            return new Object();
        }

        @Override
        protected void doBegin(Object transaction, TransactionDefinition definition) {
            isolationLevels.add(definition.getIsolationLevel());
        }

        @Override
        protected void doCommit(DefaultTransactionStatus status) {}

        @Override
        protected void doRollback(DefaultTransactionStatus status) {}
    }

    ProjectService projects = mock(ProjectService.class);
    PageService pages = mock(PageService.class);
    TemplateCatalog templates = mock(TemplateCatalog.class);
    CatalogReference catalog = mock(CatalogReference.class);
    CatalogRegistrationScheduler registrations = mock(CatalogRegistrationScheduler.class);
    PaymentAdmissions payment = mock(PaymentAdmissions.class);
    AdmissionReleaseFence releases = mock(AdmissionReleaseFence.class);
    NoTransactions transactions = new NoTransactions();
    ProjectCreationService service = new ProjectCreationService(
            projects, pages, templates, catalog, registrations, payment, releases, transactions);

    static ProjectRecord existing(UUID owner, String name, UUID businessTypeId, String catalogVersion) {
        return existing(owner, name, "kilncoffee", businessTypeId, catalogVersion);
    }

    static ProjectRecord existing(
            UUID owner, String name, String subdomain, UUID businessTypeId, String catalogVersion) {
        Instant t = Instant.parse("2026-10-15T00:00:00Z");
        return new ProjectRecord(
                PROJECT,
                owner,
                name,
                subdomain,
                businessTypeId,
                0,
                catalogVersion,
                ProjectStatus.draft,
                subdomain + ".sites.webblock.test",
                t,
                t,
                null);
    }

    private static WebBlockException invalidSubdomain() {
        return new WebBlockException(
                ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("subdomain", ErrorCode.VALUE_INVALID)));
    }

    private static Consumer<Throwable> code(ErrorCode expected) {
        return e -> assertThat(((WebBlockException) e).errorCode()).isEqualTo(expected);
    }

    /** An integrity violation as Spring reports it; the service classifies by re-reading, not by its cause (L1). */
    private static DataIntegrityViolationException violation() {
        return new DataIntegrityViolationException("x", new SQLException("x", "23505"));
    }

    @BeforeEach
    void choicesAreAvailable() {
        when(projects.find(PROJECT)).thenReturn(Optional.empty());
        when(catalog.activeBusinessType("clothing_shop")).thenReturn(Optional.of(TYPE));
        when(catalog.businessType("clothing_shop")).thenReturn(Optional.of(TYPE));
        when(templates.find("blank", "clothing_shop")).thenReturn(Optional.of(TEMPLATE));
        when(payment.admitWebsite(OWNER, PROJECT)).thenReturn(new WebsiteAdmission(AdmissionDecision.ADMITTED, null));
        when(projects.create(any())).thenAnswer(inv -> existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0"));
        when(releases.state(PROJECT)).thenReturn(NO_RELEASE);
        when(releases.lockAndRead(PROJECT)).thenReturn(NO_RELEASE);
    }

    @Test
    @DisplayName("FR-13.1: the sub-domain is checked, then found free before Payment, then stored with the project")
    void fr13_1_aNewProjectIsAdmittedThenInsertedWithItsPagesAndRegistration() {
        CreationOutcome outcome = service.create(OWNER, PROJECT, COMMAND);
        assertThat(outcome.created()).isTrue();
        assertThat(outcome.project().subdomain()).isEqualTo("kilncoffee");
        InOrder order = inOrder(catalog, releases, payment, projects, pages, registrations);
        order.verify(projects).requireValidSubdomain("kilncoffee");
        order.verify(projects).find(PROJECT);
        order.verify(catalog).activeBusinessType("clothing_shop");
        order.verify(catalog).websiteExists(PROJECT);
        order.verify(projects).subdomainTaken("kilncoffee");
        order.verify(releases).state(PROJECT);
        order.verify(payment).admitWebsite(OWNER, PROJECT);
        order.verify(releases).lockAndRead(PROJECT);
        order.verify(projects)
                .create(new NewProject(PROJECT, OWNER, "My shop", "kilncoffee", TYPE.businessTypeId(), "0.1.0"));
        order.verify(pages)
                .insertInitialPages(
                        PROJECT,
                        "0.1.0",
                        List.of(new NewPage(
                                "Home", "/", TEMPLATE.pages().getFirst().puckTree(), null, null, 0)));
        order.verify(registrations).schedule(eq(PROJECT), eq(COMMAND.businessProfile()));
        verify(releases, never()).schedule(any());
    }

    @Test
    @DisplayName("FR-13.2: a taken sub-domain is HOSTNAME_TAKEN; Payment not asked; no project")
    void fr13_2_aTakenSubdomainIsRefusedBeforePayment() {
        when(projects.subdomainTaken("kilncoffee")).thenReturn(true);
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).satisfies(code(ErrorCode.HOSTNAME_TAKEN));
        verifyNoInteractions(payment);
        verify(projects, never()).create(any());
        verify(releases, never()).schedule(any());
    }

    /** A concurrent create of this very project won and holds the sub-domain: a repeat (P7), not a taken name. */
    @Test
    @DisplayName("FR-13.2: the sub-domain held by a concurrent create of this project is a repeat")
    void fr13_2_theSubdomainOfAConcurrentWinnerIsARepeat() {
        when(projects.subdomainTaken("kilncoffee")).thenReturn(true);
        when(projects.find(PROJECT))
                .thenReturn(Optional.empty())
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        assertThat(service.create(OWNER, PROJECT, COMMAND).created()).isFalse();
        verifyNoInteractions(payment);
    }

    @Test
    @DisplayName("FR-13.3: a refused sub-domain stops the request before anything is read or asked")
    void fr13_3_aRefusedSubdomainIsCheckedFirst() {
        WebBlockException refused = invalidSubdomain();
        doThrow(refused).when(projects).requireValidSubdomain("kilncoffee");
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(refused);
        verify(projects, never()).find(any());
        verifyNoInteractions(catalog, payment, pages, registrations);
        verify(projects, never()).create(any());
    }

    @Test
    @DisplayName("FR-13.4: the website exists with kilncoffee; the same PUT with kiln2 is PROJECT_ID_CONFLICT")
    void fr13_4_aRepeatWithAnotherSubdomainIsAConflict() {
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        CreateProjectCommand other =
                new CreateProjectCommand("My shop", "kiln2", "clothing_shop", "blank", COMMAND.businessProfile());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, other)).satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        verifyNoInteractions(payment);
        verify(projects, never()).subdomainTaken(any());
    }

    /**
     * Two creates with different IDs and one sub-domain both find it free; the unique key decides. The loser's insert
     * fails, no project has its ID, so its admission is released and the violation (HOSTNAME_TAKEN) goes back.
     */
    @Test
    @DisplayName("FR-13.2: a create losing the sub-domain race releases its admission and reports the violation")
    void fr13_2_aLostSubdomainRaceReleasesTheAdmission() {
        DataIntegrityViolationException lost = violation();
        when(projects.create(any())).thenThrow(lost);
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(lost);
        verify(payment).admitWebsite(OWNER, PROJECT);
        verify(releases).schedule(PROJECT);
    }

    /** The release fence is correct only under READ COMMITTED: it must see what the lock holder committed. */
    @Test
    void theInsertAndTheReleaseDecisionRunReadCommitted() {
        service.create(OWNER, PROJECT, COMMAND);
        when(projects.find(PROJECT)).thenReturn(Optional.empty());
        when(projects.create(any())).thenThrow(violation());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .isInstanceOf(DataIntegrityViolationException.class);
        verify(releases).schedule(PROJECT);
        assertThat(transactions.isolationLevels)
                .hasSize(3)
                .containsOnly(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    @Test
    void aRefusalCreatesNothing() {
        when(payment.admitWebsite(OWNER, PROJECT))
                .thenReturn(new WebsiteAdmission(AdmissionDecision.REFUSED, AdmissionRefusal.TRIAL_ENDED));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).satisfies(code(ErrorCode.TRIAL_ENDED));
        verify(projects, never()).create(any());
        verify(releases, never()).schedule(any());
    }

    @Test
    void everyContractReasonMapsToItsOwnCode() {
        for (AdmissionRefusal reason : AdmissionRefusal.values()) {
            if (reason == AdmissionRefusal.OTHER) {
                continue;
            }
            when(payment.admitWebsite(OWNER, PROJECT))
                    .thenReturn(new WebsiteAdmission(AdmissionDecision.REFUSED, reason));
            assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                    .satisfies(code(ErrorCode.fromCode(reason.name()).orElseThrow()));
        }
    }

    @Test
    void anUnknownRefusalReasonIsWebsiteNotAdmitted() {
        when(payment.admitWebsite(OWNER, PROJECT))
                .thenReturn(new WebsiteAdmission(AdmissionDecision.REFUSED, AdmissionRefusal.OTHER));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.WEBSITE_NOT_ADMITTED));
    }

    @Test
    void aRepeatWithTheSameBodyReturnsTheProjectWithoutAskingPayment() {
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        CreationOutcome outcome = service.create(OWNER, PROJECT, COMMAND);
        assertThat(outcome.created()).isFalse();
        verifyNoInteractions(payment);
        verify(projects, never()).create(any());
    }

    @Test
    void aRepeatWithAnotherBodyOrByAnotherOwnerIsAConflict() {
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(OWNER, "Other name", TYPE.businessTypeId(), "0.1.0")));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(UUID.randomUUID(), "My shop", TYPE.businessTypeId(), "0.1.0")));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        when(projects.find(PROJECT)).thenReturn(Optional.of(existing(OWNER, "My shop", UUID.randomUUID(), "0.1.0")));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "9.9.9")));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        verifyNoInteractions(payment);
    }

    @Test
    void aRepeatNamingAVanishedBusinessTypeOrTemplateIsAConflict() {
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        when(catalog.businessType("clothing_shop")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        when(catalog.businessType("clothing_shop")).thenReturn(Optional.of(TYPE));
        when(templates.find("blank", "clothing_shop")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        verifyNoInteractions(payment);
    }

    @Test
    void aRepeatAfterTheBusinessTypeWasRetiredStillReturnsTheProject() {
        BusinessType retired = new BusinessType(TYPE.businessTypeId(), TYPE.code(), TYPE.name(), false);
        when(catalog.businessType("clothing_shop")).thenReturn(Optional.of(retired));
        when(catalog.activeBusinessType("clothing_shop")).thenReturn(Optional.empty());
        when(projects.find(PROJECT))
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        assertThat(service.create(OWNER, PROJECT, COMMAND).created()).isFalse();
    }

    @Test
    void anInactiveBusinessTypeOrUnknownTemplateIsRefusedBeforePayment() {
        when(catalog.activeBusinessType("clothing_shop")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.BUSINESS_TYPE_NOT_AVAILABLE));
        when(catalog.activeBusinessType("clothing_shop")).thenReturn(Optional.of(TYPE));
        when(templates.find(anyString(), anyString())).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.TEMPLATE_NOT_AVAILABLE));
        verifyNoInteractions(payment);
    }

    @Test
    void anIdTheCatalogAlreadyHoldsIsAConflictBeforePayment() {
        when(catalog.websiteExists(PROJECT)).thenReturn(true);
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        verifyNoInteractions(payment);
        verify(projects, never()).create(any());
        verify(releases, never()).schedule(any());
    }

    @Test
    void aConcurrentCreateAlreadyInTheCatalogIsARepeat() {
        when(catalog.websiteExists(PROJECT)).thenReturn(true);
        when(projects.find(PROJECT))
                .thenReturn(Optional.empty())
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        assertThat(service.create(OWNER, PROJECT, COMMAND).created()).isFalse();
        verifyNoInteractions(payment);
    }

    @Test
    void anUnavailableCatalogIsRefusedBeforePayment() {
        when(catalog.websiteExists(PROJECT)).thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.CATALOG_UNAVAILABLE));
        verifyNoInteractions(payment);
        verify(projects, never()).create(any());
    }

    @Test
    void aPermanentInsertFailureReleasesTheAdmissionDurably() {
        DataIntegrityViolationException failure = violation();
        when(projects.create(any())).thenThrow(failure);
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(failure);
        verify(releases).schedule(PROJECT);
    }

    @Test
    void aRefusalInsideTheTransactionReleasesTheAdmissionDurably() {
        WebBlockException refused = new WebBlockException(ErrorCode.PUCK_TREE_INVALID);
        doThrow(refused).when(pages).insertInitialPages(eq(PROJECT), anyString(), anyList());
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(refused);
        verify(releases).schedule(PROJECT);
    }

    @Test
    void aFailedReleaseSchedulingStillReportsTheOriginalFailure() {
        DataIntegrityViolationException failure = violation();
        when(projects.create(any())).thenThrow(failure);
        doThrow(new QueryTimeoutException("down")).when(releases).schedule(PROJECT);
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(failure);
    }

    @Test
    void aDuplicateIdRaceReturnsTheExistingProject() {
        when(projects.create(any())).thenThrow(violation());
        when(projects.find(PROJECT))
                .thenReturn(Optional.empty())
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        assertThat(service.create(OWNER, PROJECT, COMMAND).created()).isFalse();
        verify(releases, never()).schedule(any());
    }

    @Test
    void aDuplicateIdRaceWithAnotherBodyIsAConflict() {
        when(projects.create(any())).thenThrow(violation());
        when(projects.find(PROJECT))
                .thenReturn(Optional.empty())
                .thenReturn(Optional.of(existing(OWNER, "Other", TYPE.businessTypeId(), "0.1.0")));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        verify(releases, never()).schedule(any());
    }

    @Test
    void anIntegrityViolationWithNoProjectOnRereadReleasesTheAdmission() {
        DataIntegrityViolationException failure = violation();
        when(projects.create(any())).thenThrow(failure);
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(failure);
        verify(releases).schedule(PROJECT);
    }

    @Test
    void aTransientFailureKeepsTheAdmissionForTheClientsRetry() {
        when(projects.create(any())).thenThrow(new QueryTimeoutException("slow"));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isInstanceOf(QueryTimeoutException.class);
        verify(releases, never()).schedule(any());
    }

    @Test
    void anUnavailablePaymentCreatesNothing() {
        when(payment.admitWebsite(OWNER, PROJECT)).thenThrow(new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PAYMENT_UNAVAILABLE));
        verify(projects, never()).create(any());
        verify(releases, never()).schedule(any());
    }

    @Test
    void anotherClientAdmittedOnThisIdAtPaymentIsAConflictAndCreatesNothing() {
        when(payment.admitWebsite(OWNER, PROJECT)).thenThrow(new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.PROJECT_ID_CONFLICT));
        verify(projects, never()).create(any());
        verify(releases, never()).schedule(any());
    }

    @Test
    void aPendingReleaseRefusesTheRetryBeforePayment() {
        when(releases.state(PROJECT)).thenReturn(new AdmissionReleaseFence.State(1, 1));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                .satisfies(code(ErrorCode.ADMISSION_RELEASE_PENDING));
        verifyNoInteractions(payment);
        verify(projects, never()).create(any());
        verify(releases, never()).schedule(any());
    }

    @Test
    void aCompletedEarlierReleaseLetsTheRetryCreateTheProject() {
        AdmissionReleaseFence.State done = new AdmissionReleaseFence.State(1, 0);
        when(releases.state(PROJECT)).thenReturn(done);
        when(releases.lockAndRead(PROJECT)).thenReturn(done);
        assertThat(service.create(OWNER, PROJECT, COMMAND).created()).isTrue();
        verify(releases, never()).schedule(any());
    }

    /**
     * The fence's second half: a release scheduled after this request looked, even one already completed, may have
     * freed the admission Payment just returned (it is idempotent on the project ID), so the insert is refused and
     * this request's admission is released too.
     */
    @Test
    void aReleaseScheduledSinceThePreCheckRefusesTheInsert() {
        for (AdmissionReleaseFence.State since :
                List.of(new AdmissionReleaseFence.State(1, 1), new AdmissionReleaseFence.State(1, 0))) {
            when(releases.lockAndRead(PROJECT)).thenReturn(since);
            assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND))
                    .satisfies(code(ErrorCode.ADMISSION_RELEASE_PENDING));
        }
        verify(projects, never()).create(any());
        verify(releases, org.mockito.Mockito.times(2)).schedule(PROJECT);
    }

    @Test
    void noReleaseIsScheduledWhenTheProjectExistsUnderTheFence() {
        WebBlockException refused = new WebBlockException(ErrorCode.PUCK_TREE_INVALID);
        doThrow(refused).when(pages).insertInitialPages(eq(PROJECT), anyString(), anyList());
        // A concurrent request with the same ID committed the project before this one's release decision.
        when(projects.find(PROJECT))
                .thenReturn(Optional.empty())
                .thenReturn(Optional.of(existing(OWNER, "My shop", TYPE.businessTypeId(), "0.1.0")));
        assertThatThrownBy(() -> service.create(OWNER, PROJECT, COMMAND)).isSameAs(refused);
        InOrder order = inOrder(releases, projects);
        order.verify(releases).lockAndRead(PROJECT);
        order.verify(projects).create(any());
        order.verify(releases).lockAndRead(PROJECT);
        order.verify(projects).find(PROJECT);
        verify(releases, never()).schedule(any());
    }
}
