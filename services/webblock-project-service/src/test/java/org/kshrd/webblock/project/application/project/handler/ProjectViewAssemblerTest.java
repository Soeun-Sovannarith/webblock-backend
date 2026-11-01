package org.kshrd.webblock.project.application.project.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.project.model.ProjectView;
import org.kshrd.webblock.project.application.template.handler.TemplateCatalog;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;

class ProjectViewAssemblerTest {

    static final UUID FOOD = UUID.randomUUID();
    static final UUID TECH = UUID.randomUUID();

    CatalogReference catalog = mock(CatalogReference.class);
    PageService pages = mock(PageService.class);
    TemplateCatalog templates = mock(TemplateCatalog.class);
    GenerationRequestService requests = mock(GenerationRequestService.class);
    ProjectViewAssembler assembler = new ProjectViewAssembler(catalog, pages, templates, requests);

    private static ProjectRecord project(UUID businessTypeId) {
        Instant t = Instant.parse("2026-10-15T00:00:00Z");
        return new ProjectRecord(
                UUID.randomUUID(),
                UUID.randomUUID(),
                "Shop",
                "shop" + UUID.randomUUID().toString().substring(0, 6),
                businessTypeId,
                0,
                "0.1.0",
                ProjectStatus.draft,
                "shop.sites.test",
                t,
                t,
                null);
    }

    @Test
    @DisplayName("FR-14.2: a page of projects costs one catalog query and one pages query, however many projects")
    void fr14_2_onePageIsOneQueryEach() {
        ProjectRecord a = project(FOOD);
        ProjectRecord b = project(TECH);
        ProjectRecord c = project(FOOD);
        when(catalog.businessTypes(anyCollection()))
                .thenReturn(Map.of(
                        FOOD, new BusinessType(FOOD, "food_shop", "Foods", true),
                        TECH, new BusinessType(TECH, "tech_shop", "Technology", true)));
        when(pages.homeTemplateKeys(anyCollection())).thenReturn(Map.of());
        when(templates.thumbnailForStyleKey(null)).thenReturn(Optional.empty());

        List<ProjectView> views = assembler.views(List.of(a, b, c));

        assertThat(views)
                .extracting(ProjectView::businessTypeCode)
                .containsExactly("food_shop", "tech_shop", "food_shop");
        assertThat(views.get(1).businessTypeName()).isEqualTo("Technology");
        verify(catalog, times(1)).businessTypes(List.of(FOOD, TECH));
        verify(catalog, never()).businessType(FOOD);
        verify(pages, times(1)).homeTemplateKeys(List.of(a.projectId(), b.projectId(), c.projectId()));
        verify(requests, times(1)).lastDeployedEditRevisions(List.of(a.projectId(), b.projectId(), c.projectId()));
    }

    @Test
    @DisplayName("FR-15.2: the thumbnail follows the home page's style key; no key or an unknown key is null")
    void fr15_2_thumbnailFollowsTheStyleKey() {
        ProjectRecord styled = project(FOOD);
        ProjectRecord blank = project(FOOD);
        ProjectRecord unknown = project(FOOD);
        when(catalog.businessTypes(anyCollection())).thenReturn(Map.of());
        when(pages.homeTemplateKeys(anyCollection()))
                .thenReturn(Map.of(styled.projectId(), "sweet-pastel", unknown.projectId(), "retired-style"));
        when(templates.thumbnailForStyleKey("sweet-pastel"))
                .thenReturn(Optional.of("/mock/templates/sweet-pastel.png"));
        when(templates.thumbnailForStyleKey("retired-style")).thenReturn(Optional.empty());
        when(templates.thumbnailForStyleKey(null)).thenReturn(Optional.empty());

        List<ProjectView> views = assembler.views(List.of(styled, blank, unknown));

        assertThat(views)
                .extracting(ProjectView::thumbnailUrl)
                .containsExactly("/mock/templates/sweet-pastel.png", null, null);
    }

    @Test
    @DisplayName("FR-15.2: a business type with no catalog row gives null code and name, not an error")
    void fr15_2_aMissingCatalogRowIsNull() {
        when(catalog.businessTypes(anyCollection())).thenReturn(Map.of());
        when(pages.homeTemplateKeys(anyCollection())).thenReturn(Map.of());
        when(templates.thumbnailForStyleKey(null)).thenReturn(Optional.empty());

        ProjectView view = assembler.view(project(FOOD));

        assertThat(view.businessTypeCode()).isNull();
        assertThat(view.businessTypeName()).isNull();
        assertThat(view.businessTypeId()).isEqualTo(FOOD);
    }

    @Test
    @DisplayName("FR-14.4: with the catalog down the read still answers, names null, thumbnail kept")
    void fr14_4_catalogDownDegradesToNullNames() {
        ProjectRecord styled = project(FOOD);
        when(catalog.businessTypes(anyCollection())).thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));
        when(pages.homeTemplateKeys(anyCollection())).thenReturn(Map.of(styled.projectId(), "clean-mono"));
        when(templates.thumbnailForStyleKey("clean-mono")).thenReturn(Optional.of("/mock/templates/clean-mono.png"));

        ProjectView view = assembler.view(styled);

        assertThat(view.businessTypeCode()).isNull();
        assertThat(view.businessTypeName()).isNull();
        assertThat(view.thumbnailUrl()).isEqualTo("/mock/templates/clean-mono.png");
    }

    @Test
    @DisplayName("FR-14.4: any other refusal from the catalog read is not swallowed")
    void fr14_4_otherErrorsPropagate() {
        when(catalog.businessTypes(anyCollection())).thenThrow(new WebBlockException(ErrorCode.VALIDATION_FAILED));

        assertThatThrownBy(() -> assembler.view(project(FOOD))).isInstanceOf(WebBlockException.class);
    }

    private static ProjectRecord atRevision(ProjectRecord p, long editRevision) {
        return new ProjectRecord(
                p.projectId(),
                p.ownerUserId(),
                p.name(),
                p.subdomain(),
                p.businessTypeId(),
                editRevision,
                p.componentCatalogVersion(),
                p.status(),
                p.platformHostname(),
                p.createdAt(),
                p.updatedAt(),
                p.deletedAt());
    }

    @Test
    @DisplayName(
            "FR-14.8: never deployed is undeployed; at the last deployed revision it is not; a later save is again")
    void fr14_8_undeployedChangesFollowTheDeployedRevision() {
        ProjectRecord never = atRevision(project(FOOD), 0);
        ProjectRecord current = atRevision(project(FOOD), 5);
        ProjectRecord later = atRevision(project(FOOD), 6);
        when(catalog.businessTypes(anyCollection())).thenReturn(Map.of());
        when(pages.homeTemplateKeys(anyCollection())).thenReturn(Map.of());
        when(templates.thumbnailForStyleKey(null)).thenReturn(Optional.empty());
        when(requests.lastDeployedEditRevisions(anyCollection()))
                .thenReturn(Map.of(current.projectId(), 5L, later.projectId(), 5L));

        List<ProjectView> views = assembler.views(List.of(never, current, later));

        assertThat(views).extracting(ProjectView::hasUndeployedChanges).containsExactly(true, false, true);
        assertThat(views).extracting(ProjectView::lastDeployedEditRevision).containsExactly(null, 5L, 5L);
    }

    @Test
    @DisplayName("FR-14.2: an empty page asks neither the catalog nor the pages")
    void fr14_2_anEmptyPageAsksNothing() {
        assertThat(assembler.views(List.of())).isEmpty();
        verifyNoInteractions(catalog, pages, templates, requests);
    }
}
