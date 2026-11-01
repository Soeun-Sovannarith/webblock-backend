package org.kshrd.webblock.project.application.wizard.handler;

import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.job.CatalogRegistrationScheduler;
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
import org.kshrd.webblock.project.application.wizard.model.CreateProjectCommand;
import org.kshrd.webblock.project.application.wizard.model.CreationOutcome;
import org.kshrd.webblock.project.application.wizard.model.HomePageDetails;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The creation wizard (spec 5.1). Idempotent on the client-generated project ID without an extra table: a repeat
 * with the same owner, name, sub-domain, business type and template catalog returns the project (decision P7). The
 * sub-domain is checked, then found free before Payment is asked (FR-13). Payment is asked outside any transaction;
 * the project, its template pages and the catalog registration job are written in one transaction.
 *
 * <p>Not transactional itself: each read before Payment's call commits on its own, so no project-database transaction
 * stays open while Payment answers. Its own transactions run READ COMMITTED, which the release fence needs.
 */
@Service
public class ProjectCreationService {

    private static final Logger log = LoggerFactory.getLogger(ProjectCreationService.class);

    private final ProjectService projects;
    private final PageService pages;
    private final TemplateCatalog templates;
    private final CatalogReference catalog;
    private final CatalogRegistrationScheduler registrations;
    private final PaymentAdmissions payment;
    private final AdmissionReleaseFence releases;
    private final TransactionTemplate transaction;

    public ProjectCreationService(
            ProjectService projects,
            PageService pages,
            TemplateCatalog templates,
            CatalogReference catalog,
            CatalogRegistrationScheduler registrations,
            PaymentAdmissions payment,
            AdmissionReleaseFence releases,
            PlatformTransactionManager transactions) {
        this.projects = projects;
        this.pages = pages;
        this.templates = templates;
        this.catalog = catalog;
        this.registrations = registrations;
        this.payment = payment;
        this.releases = releases;
        // Pinned, so a server, role or pool default cannot weaken the release fence (see AdmissionReleaseFence).
        this.transaction = new TransactionTemplate(transactions);
        this.transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    /**
     * Any failure other than a refusal (timeouts, lost connections) may have committed: the admission is then kept,
     * and the client's retry with the same projectId either finds the project or is admitted again idempotently.
     *
     * <p>A release of this project's admission, scheduled after a permanent failure, must never free the admission of a
     * project that exists. Payment is idempotent on the project ID, so a retry is handed the
     * very admission a pending release is about to free. The retry is therefore refused (ADMISSION_RELEASE_PENDING)
     * when a release is pending before Payment is asked, and when any release was scheduled between that look and the
     * insert, which runs under the project's release fence.
     */
    public CreationOutcome create(UUID ownerUserId, UUID projectId, CreateProjectCommand command) {
        projects.requireValidSubdomain(command.subdomain());
        Optional<ProjectRecord> existing = projects.find(projectId);
        if (existing.isPresent()) {
            return repeat(existing.get(), ownerUserId, command);
        }
        BusinessType type = catalog.activeBusinessType(command.businessTypeCode())
                .orElseThrow(() -> new WebBlockException(ErrorCode.BUSINESS_TYPE_NOT_AVAILABLE));
        SiteTemplate template = templates
                .find(command.templateCode(), type.code())
                .orElseThrow(() -> new WebBlockException(ErrorCode.TEMPLATE_NOT_AVAILABLE));
        if (catalog.websiteExists(projectId)) {
            // A website registered under this ID outside this service (a demo shop, say) would fail the registration
            // after Payment admitted it, so the ID is refused first. A concurrent create that won and is registered
            // already is a repeat (P7).
            return projects.find(projectId)
                    .map(winner -> repeat(winner, ownerUserId, command))
                    .orElseThrow(() -> new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT));
        }
        if (projects.subdomainTaken(command.subdomain())) {
            // Held by any project, a deleted one included, so Payment is not asked. A concurrent create of this very
            // project that won holds it too: that is a repeat (P7). Two creates that both find it free reach the
            // insert, where projects_subdomain_key lets one win; the loser releases its admission (below).
            return projects.find(projectId)
                    .map(winner -> repeat(winner, ownerUserId, command))
                    .orElseThrow(() -> new WebBlockException(ErrorCode.HOSTNAME_TAKEN));
        }
        // One live website per name for the owner: a refusal before Payment is asked, so nothing is admitted.
        projects.requireNameFree(ownerUserId, command.name());
        // The releases counted here completed before Payment is asked: the admission it returns is not one they free.
        AdmissionReleaseFence.State before = releases.state(projectId);
        if (before.pending()) {
            throw new WebBlockException(ErrorCode.ADMISSION_RELEASE_PENDING);
        }

        WebsiteAdmission admission = payment.admitWebsite(ownerUserId, projectId);
        if (admission.decision() == AdmissionDecision.REFUSED) {
            throw new WebBlockException(refused(admission.reason()));
        }
        try {
            ProjectRecord created = transaction.execute(
                    status -> insert(ownerUserId, projectId, command, type, template, before.scheduled()));
            log.info("Project {} created", projectId);
            return new CreationOutcome(created, true);
        } catch (DataIntegrityViolationException e) {
            // Classified by what is there now: a concurrent create with the same ID won, so this is a repeat (P7), and
            // the admission stays, as Payment keys it on the project ID. Otherwise the failure is permanent.
            Optional<ProjectRecord> winner = projects.find(projectId);
            if (winner.isPresent()) {
                return repeat(winner.get(), ownerUserId, command);
            }
            releaseLater(projectId);
            throw e;
        } catch (WebBlockException e) {
            releaseLater(projectId);
            throw e;
        }
    }

    private ProjectRecord insert(
            UUID ownerUserId,
            UUID projectId,
            CreateProjectCommand command,
            BusinessType type,
            SiteTemplate template,
            long releasesSeenBefore) {
        if (releases.lockAndRead(projectId).scheduled() != releasesSeenBefore) {
            // A release scheduled since the look before Payment may have freed (or be about to free) the admission
            // Payment returned: refused, and this request's admission is released as well (the catch in create).
            throw new WebBlockException(ErrorCode.ADMISSION_RELEASE_PENDING);
        }
        ProjectRecord project = projects.create(new NewProject(
                projectId,
                ownerUserId,
                command.name(),
                command.subdomain(),
                type.businessTypeId(),
                template.componentCatalogVersion()));
        pages.insertInitialPages(
                projectId,
                template.componentCatalogVersion(),
                template.pages().stream()
                        .map(p -> new NewPage(
                                p.name(),
                                p.path(),
                                "/".equals(p.path())
                                        ? HomePageDetails.fill(p.puckTree(), command.businessProfile())
                                        : p.puckTree(),
                                p.seoTitle(),
                                p.seoDescription(),
                                p.sortOrder()))
                        .toList());
        registrations.schedule(projectId, command.businessProfile());
        return project;
    }

    private static ErrorCode refused(AdmissionRefusal reason) {
        return switch (reason) {
            case WEBSITE_LIMIT_REACHED -> ErrorCode.WEBSITE_LIMIT_REACHED;
            case TRIAL_ENDED -> ErrorCode.TRIAL_ENDED;
            case NO_ACTIVE_PLAN -> ErrorCode.NO_ACTIVE_PLAN;
            case CLIENT_SUSPENDED -> ErrorCode.CLIENT_SUSPENDED;
            case OTHER -> ErrorCode.WEBSITE_NOT_ADMITTED;
        };
    }

    /**
     * Decision P7: the same owner, name, sub-domain, business type and template catalog; anything else is
     * PROJECT_ID_CONFLICT (FR-13.4 for another sub-domain).
     */
    private CreationOutcome repeat(ProjectRecord existing, UUID ownerUserId, CreateProjectCommand command) {
        if (existing.deleted()
                || !existing.ownerUserId().equals(ownerUserId)
                || !existing.name().equals(command.name())
                || !existing.subdomain().equals(command.subdomain())) {
            throw new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT);
        }
        // Not activeBusinessType: a repeat after the type was retired still names the project's type.
        Optional<SiteTemplate> template = catalog.businessType(command.businessTypeCode())
                .filter(type -> type.businessTypeId().equals(existing.businessTypeId()))
                .flatMap(type -> templates.find(command.templateCode(), type.code()))
                .filter(t -> t.componentCatalogVersion().equals(existing.componentCatalogVersion()));
        if (template.isEmpty()) {
            throw new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT);
        }
        return new CreationOutcome(existing, false);
    }

    /**
     * Spec 5.1 step 6, made durable (decision P9): the job releases Payment's admission after this commits. Under the
     * project's release fence, and only while no project with this ID exists: a concurrent request that created it
     * uses the same admission.
     */
    private void releaseLater(UUID projectId) {
        try {
            transaction.executeWithoutResult(status -> {
                releases.lockAndRead(projectId);
                if (projects.find(projectId).isEmpty()) {
                    releases.schedule(projectId);
                } else {
                    log.info("Project {} exists: its website admission is kept", projectId);
                }
            });
        } catch (RuntimeException e) {
            log.error("Could not schedule the release of the website admission of project {}", projectId, e);
        }
    }
}
