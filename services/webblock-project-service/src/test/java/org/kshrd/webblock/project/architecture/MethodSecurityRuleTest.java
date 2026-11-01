package org.kshrd.webblock.project.architecture;

import static org.assertj.core.api.Assertions.assertThat;

import com.tngtech.archunit.core.domain.JavaClasses;
import com.tngtech.archunit.core.importer.ClassFileImporter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.architecture.fixture.GuardedApiController;
import org.kshrd.webblock.project.architecture.fixture.GuardedInternalController;
import org.kshrd.webblock.project.architecture.fixture.MetaMappedGuardedController;
import org.kshrd.webblock.project.architecture.fixture.MethodSecurityFixtures;
import org.kshrd.webblock.project.architecture.fixture.UnguardedInternalController;

class MethodSecurityRuleTest {

    private static boolean violates(Class<?> fixture) {
        JavaClasses classes = new ClassFileImporter().importClasses(fixture);
        return ArchitectureRulesTest.noMethodSecurityAnywhere.evaluate(classes).hasViolation();
    }

    @Test
    void theRuleCatchesAPlantedPreAuthorizeOnAnApiHandler() {
        assertThat(violates(GuardedApiController.class)).isTrue();
    }

    @Test
    void theRuleCatchesAPlantedPreAuthorizeOnAnInternalHandler() {
        assertThat(violates(GuardedInternalController.class)).isTrue();
    }

    @Test
    void theRuleCatchesAClassLevelGuardOnAController() {
        assertThat(violates(MetaMappedGuardedController.class)).isTrue();
    }

    @ParameterizedTest
    @ValueSource(
            classes = {
                MethodSecurityFixtures.PostAuthorizeOnAMethod.class,
                MethodSecurityFixtures.PreFilterOnAMethod.class,
                MethodSecurityFixtures.PostFilterOnAMethod.class,
                MethodSecurityFixtures.SecuredOnTheClass.class,
                MethodSecurityFixtures.RolesAllowedOnTheClass.class,
                MethodSecurityFixtures.PermitAllOnAMethod.class,
                MethodSecurityFixtures.DenyAllOnTheClass.class,
                MethodSecurityFixtures.AuthorizeReturnObjectOnAMethod.class,
                MethodSecurityFixtures.MetaAnnotatedMethod.class,
                MethodSecurityFixtures.AdminOnly.class
            })
    void theRuleCatchesEveryMethodSecurityAnnotation(Class<?> fixture) {
        assertThat(violates(fixture)).as(fixture.getSimpleName()).isTrue();
    }

    @Test
    void theRuleAcceptsAHandlerWithoutMethodSecurity() {
        assertThat(violates(UnguardedInternalController.class)).isFalse();
    }
}
