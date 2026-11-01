package org.kshrd.webblock.project.architecture;

import static org.assertj.core.api.Assertions.assertThat;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.infrastructure.security.DevTokenIntrospector;

class StandInRuleTest {

    @Test
    void theRuleAcceptsTheDevTokenStandIn() {
        var classes = new ClassFileImporter().importClasses(DevTokenIntrospector.class);
        assertThat(ArchitectureRulesTest.standInsAreOnlyInThePrototypeProfile
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
    }
}
