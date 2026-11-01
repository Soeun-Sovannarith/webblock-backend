package org.kshrd.webblock.project.application.catalog.handler;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.config.ConfigurableListableBeanFactory;

@ServiceIntegrationTest
class CatalogReferenceOrderTest {

    @Autowired
    ConfigurableListableBeanFactory beans;

    @Autowired
    CatalogReference reference;

    @Test
    void theCatalogIsMigratedBeforeTheReferenceReadsIt() {
        assertThat(beans.getDependenciesForBean("catalogReference")).contains("catalogMigrations");
        assertThat(beans.containsBean("catalogMigrations")).isTrue();
    }

    @Test
    void theApplicationReadsTheSeedItMigrated() {
        assertThat(reference.activeBusinessTypes()).isNotEmpty();
    }
}
