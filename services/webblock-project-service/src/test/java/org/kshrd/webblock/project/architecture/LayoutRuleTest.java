package org.kshrd.webblock.project.architecture;

import static org.assertj.core.api.Assertions.assertThat;

import com.tngtech.archunit.core.domain.JavaClass;
import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.core.importer.ImportOption;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.layoutfixture.service.OldServiceFolder;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.presentation.envelope.ResponseEnvelopeAdvice;
import org.kshrd.webblock.project.domain.project.model.ProjectEntity;
import org.kshrd.webblock.project.infrastructure.layoutfixture.persistence.FeatureBeforeType;
import org.kshrd.webblock.project.infrastructure.persistence.project.ProjectRepository;
import org.kshrd.webblock.project.layoutfixture.application.handler.FeatureFirstClass;
import org.kshrd.webblock.project.presentation.controller.layoutfixture.OldControllerFolder;
import org.kshrd.webblock.project.presentation.rest.layoutfixture.DomainUsingController;

/** TR-5, decision 0016: the layout rules accept the layer-first tree of Notion 6.1 and refuse a misplaced class. */
class LayoutRuleTest {

    @Test
    @DisplayName("TR-5: a class in each layer's type and feature folder, and in common, sits in a type folder")
    void tr5_theLayerFirstTreePasses() {
        var classes = new ClassFileImporter()
                .withImportOption(ImportOption.Predefined.DO_NOT_INCLUDE_TESTS)
                .importPackages(
                        "org.kshrd.webblock.project.presentation",
                        "org.kshrd.webblock.project.application",
                        "org.kshrd.webblock.project.domain",
                        "org.kshrd.webblock.project.infrastructure",
                        "org.kshrd.webblock.project.common");
        assertThat(classes)
                .extracting(JavaClass::getName)
                .contains(
                        ProjectService.class.getName(),
                        ProjectEntity.class.getName(),
                        ProjectRepository.class.getName(),
                        ErrorCode.class.getName(),
                        ResponseEnvelopeAdvice.class.getName(),
                        "org.kshrd.webblock.project.presentation.grpc.access.AccessGrpcService",
                        "org.kshrd.webblock.project.presentation.rest.project.ProjectController");
        assertThat(ArchitectureRulesTest.layerClassesSitInATypeFolder
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
        assertThat(ArchitectureRulesTest.featureClassesSitInALayer
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
    }

    @Test
    @DisplayName("TR-5: a use case in application/<feature>/service is refused (handler is the folder)")
    void tr5_theOldServiceFolderIsRefused() {
        var classes = new ClassFileImporter().importClasses(OldServiceFolder.class);
        assertThat(ArchitectureRulesTest.layerClassesSitInATypeFolder
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-5: a controller in presentation/controller is refused (rest is the folder)")
    void tr5_theOldControllerFolderIsRefused() {
        var classes = new ClassFileImporter().importClasses(OldControllerFolder.class);
        assertThat(ArchitectureRulesTest.layerClassesSitInATypeFolder
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-5: infrastructure/<feature>/<type> is refused (the type folder comes first)")
    void tr5_featureBeforeTypeInInfrastructureIsRefused() {
        var classes = new ClassFileImporter().importClasses(FeatureBeforeType.class);
        assertThat(ArchitectureRulesTest.layerClassesSitInATypeFolder
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-5: a class in the old feature-first tree sits in no layer and is refused")
    void tr5_aFeatureFirstClassIsRefused() {
        var classes = new ClassFileImporter().importClasses(FeatureFirstClass.class);
        assertThat(ArchitectureRulesTest.featureClassesSitInALayer
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-4: a presentation class that uses a domain type is refused")
    void tr4_presentationUsingDomainIsRefused() {
        var classes = new ClassFileImporter().importClasses(DomainUsingController.class);
        assertThat(ArchitectureRulesTest.presentationDoesNotUseDomain
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-4: presentation uses no domain type, and common's presentation keeps using common's domain")
    void tr4_theRealPresentationPasses() {
        var classes = new ClassFileImporter()
                .withImportOption(ImportOption.Predefined.DO_NOT_INCLUDE_TESTS)
                .importPackages("org.kshrd.webblock.project.presentation", "org.kshrd.webblock.project.common");
        assertThat(classes).isNotEmpty();
        assertThat(ArchitectureRulesTest.presentationDoesNotUseDomain
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
    }
}
