package org.kshrd.webblock.project.architecture;

import static org.assertj.core.api.Assertions.assertThat;

import com.tngtech.archunit.core.domain.JavaClasses;
import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.core.importer.ImportOption;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.architecture.fixture.GeneratedStubUser;
import org.kshrd.webblock.project.architecture.fixture.GrpcStatusUser;
import org.kshrd.webblock.project.architecture.fixture.StrayGrpcService;

/** TR-4, design G22: the two gRPC rules catch a planted violation and accept the real gRPC classes. */
class GrpcRuleTest {

    private static JavaClasses grpcPackages() {
        return new ClassFileImporter()
                .withImportOption(ImportOption.Predefined.DO_NOT_INCLUDE_TESTS)
                .importPackages(
                        "org.kshrd.webblock.project.common.presentation.grpc",
                        "org.kshrd.webblock.project.presentation.grpc.access");
    }

    @Test
    @DisplayName("TR-4: a gRPC service outside presentation.grpc is refused")
    void tr4_aStrayServiceIsRefused() {
        var classes = new ClassFileImporter().importClasses(StrayGrpcService.class);
        assertThat(ArchitectureRulesTest.bindableServicesSitInPresentationGrpc
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-4: io.grpc or a generated proto type used outside presentation.grpc is refused")
    void tr4_grpcTypesOutsidePresentationGrpcAreRefused() {
        assertThat(ArchitectureRulesTest.onlyPresentationGrpcUsesGrpcTypes
                        .evaluate(new ClassFileImporter().importClasses(GrpcStatusUser.class))
                        .hasViolation())
                .isTrue();
        assertThat(ArchitectureRulesTest.onlyPresentationGrpcUsesGrpcTypes
                        .evaluate(new ClassFileImporter().importClasses(GeneratedStubUser.class))
                        .hasViolation())
                .isTrue();
    }

    @Test
    @DisplayName("TR-4: the server, its interceptors and CheckAccess pass both rules")
    void tr4_theRealGrpcClassesPass() {
        JavaClasses classes = grpcPackages();
        assertThat(classes).isNotEmpty();
        assertThat(ArchitectureRulesTest.bindableServicesSitInPresentationGrpc
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
        assertThat(ArchitectureRulesTest.onlyPresentationGrpcUsesGrpcTypes
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
    }
}
