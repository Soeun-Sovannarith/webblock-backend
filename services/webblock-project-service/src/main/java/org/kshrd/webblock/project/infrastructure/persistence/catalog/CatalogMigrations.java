package org.kshrd.webblock.project.infrastructure.persistence.catalog;

import org.kshrd.webblock.project.application.catalog.handler.CatalogFlyway;
import org.kshrd.webblock.project.infrastructure.config.catalog.CatalogProperties;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.stereotype.Component;

/** Brings the catalog schema up to date at start-up (webblock.catalog.migrations.enabled). */
@Component
class CatalogMigrations implements InitializingBean {

    private final CatalogProperties properties;

    CatalogMigrations(CatalogProperties properties) {
        this.properties = properties;
    }

    @Override
    public void afterPropertiesSet() {
        CatalogProperties.Migrations migrations = properties.migrations();
        if (migrations.enabled()) {
            CatalogFlyway.configure(properties.datasource().url(), migrations.user(), migrations.password())
                    .migrate();
        }
    }
}
